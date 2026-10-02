#!/usr/bin/env python3
"""LoRA fine-tune of Clef: LoRA on backbone attention/linear-attention/MLP projections + full joint head.

Backbone BF16 (frozen base, fp32 LoRA adapters via peft autocast_adapter_dtype), gradient checkpointing,
bf16 autocast; head fp32 master weights, starting from the released head (or --init-head). Batch size 1
(no padding, same as serving), gradient accumulation. Records (input_ids / spans / labels / split) come from
the head-FT cache index, so data are identical to the head-only run.

Memory: vision tower and lm_head are dropped from the GPU; the head's lexical option vectors only read
lm_head rows of option-span tokens, so a compact row matrix + remapped ids give identical values.

Outputs: <out>/best/adapter (PEFT, loadable by serve_clef.py CLEF_LORA_DIR), <out>/best/joint_head.safetensors
(+ config, fp32 copy), metrics.json. Final scoring reloads base + adapter exactly as serve_clef.py does
(PeftModel.from_pretrained -> merge_and_unload in BF16) and scores val with the BF16 head.

Usage: CUDA_VISIBLE_DEVICES=1 train_lora.py --model-dir D --cache C --out O [...]
"""
import argparse
import json
import math
import os
import random
import sys
import time

import torch
import torch.nn.functional as F

ap = argparse.ArgumentParser()
ap.add_argument("--model-dir", required=True)
ap.add_argument("--cache", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--init-head", default="")
ap.add_argument("--lr-lora", type=float, default=1e-4)
ap.add_argument("--lr-head", type=float, default=3e-5)
ap.add_argument("--wd", type=float, default=0.01)
ap.add_argument("--epochs", type=int, default=3)
ap.add_argument("--accum", type=int, default=16)
ap.add_argument("--r", type=int, default=16)
ap.add_argument("--alpha", type=int, default=32)
ap.add_argument("--lora-dropout", type=float, default=0.05)
ap.add_argument("--warmup", type=float, default=0.05)
ap.add_argument("--evals-per-epoch", type=int, default=4)
ap.add_argument("--patience", type=int, default=4)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--max-train", type=int, default=0, help="debug: limit train items")
ap.add_argument("--max-val", type=int, default=0, help="debug: limit val items")
args = ap.parse_args()

sys.path.insert(0, args.model_dir)
from joint_schema_model import EncodedQuestion, EncodedRecord, JointSchemaHead  # noqa: E402
from peft import LoraConfig, PeftModel, get_peft_model, get_peft_model_state_dict, set_peft_model_state_dict  # noqa: E402
from safetensors.torch import load_file, save_file  # noqa: E402
from transformers import Qwen3_5ForConditionalGeneration  # noqa: E402

torch.manual_seed(args.seed)
random.seed(args.seed)
dev = torch.device("cuda:0")
os.makedirs(args.out, exist_ok=True)
t_start = time.time()
TARGET = (r"model\.language_model\.layers\.\d+\.(self_attn\.(q_proj|k_proj|v_proj|o_proj)"
          r"|linear_attn\.(in_proj_qkv|in_proj_z|out_proj)|mlp\.(gate_proj|up_proj|down_proj))")

index = [json.loads(l) for l in open(os.path.join(args.cache, "index.jsonl"))]
uniq = sorted({t for e in index for q in e["questions"] for s, en in q["option_spans"] for t in e["input_ids"][s:en]})
remap = {t: i for i, t in enumerate(uniq)}


def to_item(e):
    qs = tuple(EncodedQuestion(q["question_id"], q["question_type"], tuple(q["question_span"]),
                               tuple(tuple(s) for s in q["option_spans"]), tuple(q["option_ids"]))
               for q in e["questions"])
    ids = torch.tensor(e["input_ids"], dtype=torch.long)
    head_ids = torch.zeros_like(ids)
    for q in e["questions"]:
        for s, en in q["option_spans"]:
            head_ids[s:en] = torch.tensor([remap[t] for t in e["input_ids"][s:en]])
    return {"e": e, "rec": EncodedRecord(input_ids=tuple(e["input_ids"]), questions=qs, record_id=e["case_id"]),
            "ids": ids, "head_ids": head_ids, "full_ids": ids}


items = [to_item(e) for e in index]
train = [it for it in items if it["e"]["split"] == "train"]
val = [it for it in items if it["e"]["split"] == "val"]
if args.max_train:
    train = train[:args.max_train]
if args.max_val:
    val = val[:args.max_val]
print(f"train {len(train)} val {len(val)} lexical rows {len(uniq)}", flush=True)


def load_backbone():
    bb = Qwen3_5ForConditionalGeneration.from_pretrained(args.model_dir, dtype=torch.bfloat16, device_map={"": "cuda:0"})
    bb.config.use_cache = False
    lex = bb.get_output_embeddings().weight[torch.tensor(uniq, device=dev)].detach().clone()
    return bb, lex


def strip_unused(bb):
    bb.lm_head = torch.nn.Identity()
    bb.model.visual = torch.nn.Identity()
    torch.cuda.empty_cache()


backbone, lex_w = load_backbone()
strip_unused(backbone)
lcfg = LoraConfig(r=args.r, lora_alpha=args.alpha, lora_dropout=args.lora_dropout, target_modules=TARGET, bias="none")
model = get_peft_model(backbone, lcfg)
model.print_trainable_parameters()
text_model = model.get_base_model().model.language_model
model.get_base_model().gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

cfg = json.load(open(os.path.join(args.model_dir, "joint_head_config.json")))
head = JointSchemaHead(**cfg)
head.load_state_dict(load_file(args.init_head or os.path.join(args.model_dir, "joint_head.safetensors")), strict=True)
head = head.float().to(dev)
print(f"loaded; GPU mem {torch.cuda.memory_allocated() / 2**30:.1f} GiB", flush=True)


def forward(it, tm, hd, lexw, autocast=True):
    ids = it["full_ids"].to(dev).unsqueeze(0)
    mask = torch.ones_like(ids)
    hids = it["head_ids"].to(dev).unsqueeze(0)
    ctx = torch.autocast("cuda", dtype=torch.bfloat16) if autocast else torch.autocast("cuda", enabled=False)
    with ctx:
        h = tm(input_ids=ids, attention_mask=mask, use_cache=False, return_dict=True).last_hidden_state
        return hd(h, hids, mask, [it["rec"]], lexw)[0][0].float()


@torch.no_grad()
def evaluate(tm, hd, lexw, autocast=True):
    was = hd.training
    hd.eval()
    model.eval()
    tot, correct, per = 0.0, 0, {}
    preds = {}
    for it in val:
        lg = forward(it, tm, hd, lexw, autocast)
        y = it["e"]["label"]
        tot += F.cross_entropy(lg.unsqueeze(0), torch.tensor([y], device=dev)).item()
        ok = int(lg.argmax().item() == y)
        correct += ok
        a, b = per.get(it["e"]["workflow"], (0, 0))
        per[it["e"]["workflow"]] = (a + ok, b + 1)
        preds[it["e"]["case_id"]] = int(lg.argmax().item())
    if was:
        hd.train()
        model.train()
    return {"val_loss": tot / len(val), "val_acc": correct / len(val),
            "val_acc_by_workflow": {k: a / b for k, (a, b) in sorted(per.items())}}, preds


lora_params = [p for p in model.parameters() if p.requires_grad]
opt = torch.optim.AdamW([
    {"params": lora_params, "lr": args.lr_lora, "weight_decay": 0.0},
    {"params": [p for n, p in head.named_parameters() if p.ndim >= 2 and "norm" not in n and "embedding" not in n],
     "lr": args.lr_head, "weight_decay": args.wd},
    {"params": [p for n, p in head.named_parameters() if not (p.ndim >= 2 and "norm" not in n and "embedding" not in n)],
     "lr": args.lr_head, "weight_decay": 0.0},
])
steps_per_epoch = math.ceil(len(train) / args.accum)
total_steps = steps_per_epoch * args.epochs
warm = max(1, int(args.warmup * total_steps))
sched = torch.optim.lr_scheduler.LambdaLR(
    opt, lambda s: (s + 1) / warm if s < warm else 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, total_steps - warm))))
eval_every = max(1, steps_per_epoch // args.evals_per_epoch)

base, base_preds = evaluate(text_model, head, lex_w)
print("BASELINE (zero-shot, autocast)", json.dumps(base), flush=True)
history = [{"step": 0, "epoch": 0.0, **base}]
best = {"val_acc": base["val_acc"], "val_loss": base["val_loss"], "step": 0}
best_lora = {k: v.detach().cpu().clone() for k, v in get_peft_model_state_dict(model).items()}
best_head = {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}
bad, step, stop = 0, 0, False
model.train()
head.train()
for ep in range(args.epochs):
    order = list(range(len(train)))
    random.shuffle(order)
    run_loss, run_n, t_ep = 0.0, 0, time.time()
    for b0 in range(0, len(order), args.accum):
        chunk = order[b0:b0 + args.accum]
        for j in chunk:
            it = train[j]
            lg = forward(it, text_model, head, lex_w)
            loss = F.cross_entropy(lg.unsqueeze(0), torch.tensor([it["e"]["label"]], device=dev)) / len(chunk)
            loss.backward()
            run_loss += loss.item() * len(chunk)
            run_n += 1
        gn = torch.nn.utils.clip_grad_norm_(lora_params + list(head.parameters()), 1.0).item()
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)
        step += 1
        if step % 20 == 0:
            print(f"step {step}/{total_steps} loss {run_loss / max(1, run_n):.4f} gn {gn:.2f} "
                  f"{(time.time() - t_ep) / ((b0 + len(chunk)) or 1):.3f}s/rec mem {torch.cuda.max_memory_allocated() / 2**30:.1f}G",
                  flush=True)
        if step % eval_every == 0 or step == total_steps:
            ev, _ = evaluate(text_model, head, lex_w)
            rec = {"step": step, "epoch": round(step / steps_per_epoch, 3), "train_loss": run_loss / max(1, run_n),
                   "lr_lora": sched.get_last_lr()[0], "grad_norm": gn, **ev, "elapsed_s": round(time.time() - t_start)}
            history.append(rec)
            run_loss, run_n = 0.0, 0
            better = ev["val_acc"] > best["val_acc"] or (ev["val_acc"] == best["val_acc"] and ev["val_loss"] < best["val_loss"])
            print(json.dumps(rec), "BEST" if better else "", flush=True)
            if better:
                best = {"val_acc": ev["val_acc"], "val_loss": ev["val_loss"], "step": step}
                best_lora = {k: v.detach().cpu().clone() for k, v in get_peft_model_state_dict(model).items()}
                best_head = {k: v.detach().cpu().clone() for k, v in head.state_dict().items()}
                bad = 0
            else:
                bad += 1
            json.dump({"args": vars(args), "history": history, "best": best},
                      open(os.path.join(args.out, "metrics.json"), "w"), indent=2)
            if bad >= args.patience:
                stop = True
                break
    if stop:
        print("early stop", flush=True)
        break

# ---- save best ----
bdir = os.path.join(args.out, "best")
os.makedirs(bdir, exist_ok=True)
set_peft_model_state_dict(model, best_lora)
model.save_pretrained(os.path.join(bdir, "adapter"))
save_file({k: v.to(torch.bfloat16).contiguous() for k, v in best_head.items()}, os.path.join(bdir, "joint_head.safetensors"))
save_file({k: v.to(torch.float32).contiguous() for k, v in best_head.items()}, os.path.join(bdir, "joint_head_fp32.safetensors"))
json.dump(cfg, open(os.path.join(bdir, "joint_head_config.json"), "w"), indent=2)
train_wall = round(time.time() - t_start)

# ---- serving-equivalent re-score: fresh base + PeftModel.from_pretrained + merge (bf16), bf16 head ----
del model, text_model, backbone, opt, lora_params
torch.cuda.empty_cache()
bb, lex2 = load_backbone()
bb = PeftModel.from_pretrained(bb, os.path.join(bdir, "adapter")).merge_and_unload()
strip_unused(bb)
bb.eval()
model = bb  # evaluate() toggles model.eval()/train(); keep it eval
srv_head = JointSchemaHead(**cfg)
srv_head.load_state_dict(load_file(os.path.join(bdir, "joint_head.safetensors")), strict=True)
srv_head = srv_head.to(dev, torch.bfloat16).eval()
final, final_preds = evaluate(bb.model.language_model, srv_head, lex2, autocast=False)
flips = sum(1 for k in final_preds if final_preds[k] != base_preds[k])
res = {"args": vars(args), "n_train": len(train), "n_val": len(val), "target_modules": TARGET,
       "baseline_autocast": base, "best": best, "final_serving_mode_merged_bf16": final,
       "val_pred_changes_vs_baseline": flips, "history": history, "train_wall_s": train_wall,
       "wall_s": round(time.time() - t_start)}
json.dump(res, open(os.path.join(args.out, "metrics.json"), "w"), indent=2)
json.dump(res, open(os.path.join(bdir, "metrics.json"), "w"), indent=2)
print("FINAL serving-mode (merged bf16)", json.dumps(final), "best", json.dumps(best), f"wall {res['wall_s']}s", flush=True)
