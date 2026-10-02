#!/usr/bin/env python3
"""Head-only fine-tune of the Clef joint schema head on cached frozen-backbone hidden states.

Starts from the released joint_head.safetensors. fp32 master weights + AdamW, bf16 autocast forward,
cross-entropy over each question's option logits. Early stopping / model selection on the dev-derived val
slice (val accuracy, ties -> lower val loss). Val is also scored in the serving-equivalent mode
(head cast to bf16, no autocast) -- before FT that must reproduce the live release logits stored in the cache.

Usage: CUDA_VISIBLE_DEVICES=1 train_head.py --model-dir D --cache C --out O [--lr 3e-5 --epochs 8 ...]
"""
import argparse
import copy
import json
import math
import os
import random
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

ap = argparse.ArgumentParser()
ap.add_argument("--model-dir", required=True)
ap.add_argument("--cache", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--lr", type=float, default=3e-5)
ap.add_argument("--wd", type=float, default=0.01)
ap.add_argument("--epochs", type=int, default=8)
ap.add_argument("--accum", type=int, default=16)
ap.add_argument("--dropout", type=float, default=0.0)
ap.add_argument("--warmup", type=float, default=0.05)
ap.add_argument("--patience", type=int, default=4, help="evals without improvement")
ap.add_argument("--evals-per-epoch", type=int, default=2)
ap.add_argument("--seed", type=int, default=0)
args = ap.parse_args()

sys.path.insert(0, args.model_dir)
from joint_schema_model import EncodedQuestion, EncodedRecord, JointSchemaHead  # noqa: E402
from safetensors.torch import load_file, save_file  # noqa: E402

torch.manual_seed(args.seed)
random.seed(args.seed)
dev = torch.device("cuda:0")
os.makedirs(args.out, exist_ok=True)
t_start = time.time()

meta = json.load(open(os.path.join(args.cache, "cache_meta.json")))
H = meta["hidden_size"]
hb = np.memmap(os.path.join(args.cache, "hidden.bin"), dtype=np.uint16, mode="r", shape=(meta["total_tokens"], H))
index = [json.loads(l) for l in open(os.path.join(args.cache, "index.jsonl"))]
assert len(index) == meta["n_records"], f"cache incomplete: {len(index)}/{meta['n_records']}"
lm_w = torch.load(os.path.join(args.cache, "lm_head_weight.pt")).to(dev)  # bf16, as served


def to_record(e):
    qs = tuple(EncodedQuestion(q["question_id"], q["question_type"], tuple(q["question_span"]),
                               tuple(tuple(s) for s in q["option_spans"]), tuple(q["option_ids"]))
               for q in e["questions"])
    return EncodedRecord(input_ids=tuple(e["input_ids"]), questions=qs, record_id=e["case_id"])


items = []
for e in index:
    items.append({"e": e, "rec": to_record(e), "ids": torch.tensor(e["input_ids"], dtype=torch.long)})
train = [it for it in items if it["e"]["split"] == "train"]
val = [it for it in items if it["e"]["split"] == "val"]
print(f"train {len(train)} val {len(val)}", flush=True)


def hidden_of(it):
    e = it["e"]
    a = np.asarray(hb[e["offset"]:e["offset"] + e["length"]])
    return torch.from_numpy(a.view(np.int16)).view(torch.bfloat16).to(dev, non_blocking=True).unsqueeze(0)


def run_head(head, it, autocast=True):
    h = hidden_of(it)
    ids = it["ids"].to(dev).unsqueeze(0)
    mask = torch.ones_like(ids)
    if autocast:
        with torch.autocast("cuda", dtype=torch.bfloat16):
            return head(h, ids, mask, [it["rec"]], lm_w)[0][0]
    return head(h, ids, mask, [it["rec"]], lm_w)[0][0]


cfg = json.load(open(os.path.join(args.model_dir, "joint_head_config.json")))
release_sd = load_file(os.path.join(args.model_dir, "joint_head.safetensors"))
head = JointSchemaHead(**cfg, dropout=args.dropout)
head.load_state_dict(release_sd, strict=True)
head = head.float().to(dev)


@torch.no_grad()
def evaluate(model, serving_mode=False):
    m = model
    if serving_mode:
        m = copy.deepcopy(model).to(torch.bfloat16)
    m.eval()
    tot_loss, correct, n = 0.0, 0, 0
    per_wf = {}
    max_rel_dev = 0.0
    preds = {}
    for it in val:
        logits = run_head(m, it, autocast=not serving_mode).float()
        y = it["e"]["label"]
        tot_loss += F.cross_entropy(logits.unsqueeze(0), torch.tensor([y], device=dev)).item()
        ok = int(logits.argmax().item() == y)
        correct += ok
        n += 1
        wf = it["e"]["workflow"]
        a, b = per_wf.get(wf, (0, 0))
        per_wf[wf] = (a + ok, b + 1)
        preds[it["e"]["case_id"]] = int(logits.argmax().item())
        if serving_mode:
            rel = torch.tensor(it["e"]["release_logits"], device=dev)
            max_rel_dev = max(max_rel_dev, (logits - rel).abs().max().item())
    model.train()
    out = {"val_loss": tot_loss / n, "val_acc": correct / n,
           "val_acc_by_workflow": {k: a / b for k, (a, b) in sorted(per_wf.items())}}
    if serving_mode:
        out["max_abs_logit_dev_vs_release_live"] = max_rel_dev
    return out, preds


base_srv, base_preds = evaluate(head, serving_mode=True)
base_fp, _ = evaluate(head, serving_mode=False)
print("BASELINE serving-mode", json.dumps(base_srv), "\nBASELINE fp32/autocast", json.dumps(base_fp), flush=True)

decay, no_decay = [], []
for n_, p in head.named_parameters():
    (no_decay if p.ndim < 2 or "norm" in n_ or "embedding" in n_ else decay).append(p)
opt = torch.optim.AdamW([{"params": decay, "weight_decay": args.wd}, {"params": no_decay, "weight_decay": 0.0}],
                        lr=args.lr, betas=(0.9, 0.999), eps=1e-8)
steps_per_epoch = math.ceil(len(train) / args.accum)
total_steps = steps_per_epoch * args.epochs
warm = max(1, int(args.warmup * total_steps))
sched = torch.optim.lr_scheduler.LambdaLR(
    opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, total_steps - warm))))
eval_every = max(1, steps_per_epoch // args.evals_per_epoch)

history = [{"step": 0, "epoch": 0.0, **base_fp}]
best = {"val_acc": base_fp["val_acc"], "val_loss": base_fp["val_loss"], "step": 0}
best_sd = {k: v.detach().clone() for k, v in head.state_dict().items()}
bad = 0
step = 0
head.train()
stop = False
for ep in range(args.epochs):
    order = list(range(len(train)))
    random.shuffle(order)
    run_loss, run_n = 0.0, 0
    for b0 in range(0, len(order), args.accum):
        chunk = order[b0:b0 + args.accum]
        for j in chunk:
            it = train[j]
            logits = run_head(head, it).float()
            loss = F.cross_entropy(logits.unsqueeze(0), torch.tensor([it["e"]["label"]], device=dev)) / len(chunk)
            loss.backward()
            run_loss += loss.item() * len(chunk)
            run_n += 1
        gn = torch.nn.utils.clip_grad_norm_(head.parameters(), 1.0).item()
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)
        step += 1
        if step % eval_every == 0 or step == total_steps:
            ev, _ = evaluate(head)
            rec = {"step": step, "epoch": round(step / steps_per_epoch, 3), "train_loss": run_loss / max(1, run_n),
                   "lr": sched.get_last_lr()[0], "grad_norm": gn, **ev, "elapsed_s": round(time.time() - t_start)}
            history.append(rec)
            run_loss, run_n = 0.0, 0
            better = ev["val_acc"] > best["val_acc"] or (ev["val_acc"] == best["val_acc"] and ev["val_loss"] < best["val_loss"])
            print(json.dumps(rec), "BEST" if better else "", flush=True)
            if better:
                best = {"val_acc": ev["val_acc"], "val_loss": ev["val_loss"], "step": step}
                best_sd = {k: v.detach().clone() for k, v in head.state_dict().items()}
                bad = 0
            else:
                bad += 1
                if bad >= args.patience:
                    stop = True
                    break
            json.dump({"args": vars(args), "history": history, "best": best}, open(os.path.join(args.out, "metrics.json"), "w"), indent=2)
    if stop:
        print("early stop", flush=True)
        break

head.load_state_dict(best_sd)
final_srv, final_preds = evaluate(head, serving_mode=True)
final_srv.pop("max_abs_logit_dev_vs_release_live", None)
bdir = os.path.join(args.out, "best")
os.makedirs(bdir, exist_ok=True)
save_file({k: v.detach().to("cpu", torch.bfloat16).contiguous() for k, v in best_sd.items()},
          os.path.join(bdir, "joint_head.safetensors"))
save_file({k: v.detach().to("cpu", torch.float32).contiguous() for k, v in best_sd.items()},
          os.path.join(bdir, "joint_head_fp32.safetensors"))
json.dump(cfg, open(os.path.join(bdir, "joint_head_config.json"), "w"), indent=2)
flips = sum(1 for k in final_preds if final_preds[k] != base_preds[k])
res = {"args": vars(args), "n_train": len(train), "n_val": len(val), "cache_quant": meta["quant"],
       "baseline_serving_mode": base_srv, "baseline_fp32_autocast": base_fp, "best": best,
       "final_serving_mode": final_srv, "val_pred_changes_vs_baseline": flips,
       "history": history, "wall_s": round(time.time() - t_start)}
json.dump(res, open(os.path.join(args.out, "metrics.json"), "w"), indent=2)
json.dump(res, open(os.path.join(bdir, "metrics.json"), "w"), indent=2)
print("FINAL serving-mode", json.dumps(final_srv), "best", json.dumps(best), f"wall {res['wall_s']}s", flush=True)
