#!/usr/bin/env python3
"""Fine-tune Julia-1 on the v1 DEV records from prep_julia_data.py (train split; model selection on val).

Julia is small (144M), so besides the Clef-style head-only recipe we can fine-tune the whole model:
  --mode full   all parameters (encoder + decision head)
  --mode head   encoder frozen; trains the decision head (2 transformer layers), type embedding and scorer

Starts from the released weights. fp32 master weights + AdamW, bf16 autocast forward on CUDA (as served),
cross-entropy over the option scores. Inputs are encoded exactly as served (julia.data.sequence, strict,
same max/head lengths) and batched with the release's own Collator. Evaluates val before training (zero-shot)
and `--evals-per-epoch` times per epoch in eval mode; keeps the best (val accuracy, ties -> lower val NLL)
with early stopping. The best checkpoint is written with the release's JuliaDecisionModel.save_pretrained
plus the release tokenizer, so serve_julia.py loads it with JULIA_MODEL_DIR=<out>/best.

Usage: train_julia.py --release DIR --data prep/records.jsonl --out DIR [--mode full --lr 3e-5 --epochs 6]
"""
import argparse
import json
import math
import os
import random
import shutil
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--release", required=True)
ap.add_argument("--data", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--mode", choices=["full", "head"], default="full")
ap.add_argument("--lr", type=float, default=3e-5)
ap.add_argument("--wd", type=float, default=0.01)
ap.add_argument("--epochs", type=int, default=6)
ap.add_argument("--batch", type=int, default=16, help="items per optimizer step")
ap.add_argument("--warmup", type=float, default=0.06)
ap.add_argument("--patience", type=int, default=4, help="evals without improvement")
ap.add_argument("--evals-per-epoch", type=int, default=2)
ap.add_argument("--max-length", type=int, default=8192)
ap.add_argument("--head-length", type=int, default=640)
ap.add_argument("--seed", type=int, default=0)
ap.add_argument("--device", default="cuda")
ap.add_argument("--limit", type=int, default=0, help="smoke test: use only N train / N val records")
args = ap.parse_args()

sys.path.insert(0, args.release)
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402
from julia.data import Collator, sequence  # noqa: E402
from julia.model import JuliaDecisionModel  # noqa: E402

random.seed(args.seed)
torch.manual_seed(args.seed)
dev = torch.device(args.device)
AMP = dict(device_type=dev.type, dtype=torch.bfloat16, enabled=dev.type == "cuda")
os.makedirs(args.out, exist_ok=True)
t_start = time.time()
log_fh = open(os.path.join(args.out, "train_log.jsonl"), "a")


def log(**kw):
    kw["t"] = round(time.time() - t_start, 1)
    print(json.dumps(kw), flush=True)
    log_fh.write(json.dumps(kw) + "\n")
    log_fh.flush()


tok = AutoTokenizer.from_pretrained(os.path.join(args.release, "tokenizer"), trust_remote_code=False)
recs = [json.loads(l) for l in open(args.data, encoding="utf-8")]
for r in recs:
    r["_encoded"] = sequence(tok, r, args.max_length, args.head_length, strict=True)
train = [r for r in recs if r["split"] == "train"]
val = [r for r in recs if r["split"] == "val"]
if args.limit:
    train, val = train[:args.limit], val[:args.limit]
collate = Collator(tok, args.max_length, args.head_length)

model = JuliaDecisionModel.from_pretrained(args.release, memory_map=False).float().to(dev)
if args.mode == "head":
    for p in model.encoder.parameters():
        p.requires_grad_(False)
params = [p for p in model.parameters() if p.requires_grad]
log(event="start", args=vars(args), n_train=len(train), n_val=len(val),
    trainable=sum(p.numel() for p in params), total=sum(p.numel() for p in model.parameters()))


def batches(items, size, shuffle):
    """Length-bucketed batches (bounded padding); batch order shuffled for training."""
    order = sorted(range(len(items)), key=lambda i: len(items[i]["_encoded"]["ids"]))
    if shuffle:  # jitter within neighbourhoods so batches differ between epochs
        order = sorted(order, key=lambda i: len(items[i]["_encoded"]["ids"]) * (1 + 0.1 * random.random()))
    out = [[items[i] for i in order[k:k + size]] for k in range(0, len(order), size)]
    if shuffle:
        random.shuffle(out)
    return out


def to_dev(b):
    return {k: v.to(dev) for k, v in b.items()}


@torch.no_grad()
def evaluate(items):
    model.eval()
    k = n = 0
    nll = 0.0
    per_wf = {}
    for b in batches(items, 16, False):
        x = to_dev(collate(b))
        y = x.pop("labels")
        with torch.autocast(**AMP):
            s = model(**x)
        nll += F.cross_entropy(s.float(), y, reduction="sum").item()
        pred = s.argmax(-1)
        for r, ok in zip(b, (pred == y).tolist()):
            w = per_wf.setdefault(r["workflow"], [0, 0])
            w[0] += ok
            w[1] += 1
            k += ok
            n += 1
    model.train()
    return k / n, nll / n, {w: round(v[0] / v[1], 4) for w, v in per_wf.items()}


opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=args.wd)
steps_per_epoch = math.ceil(len(train) / args.batch)
total = steps_per_epoch * args.epochs
warm = max(1, int(args.warmup * total))
sched = torch.optim.lr_scheduler.LambdaLR(  # linear warmup, then linear decay to 0
    opt, lambda s: (s + 1) / warm if s < warm else max(0.0, (total - s) / max(1, total - warm)))
eval_every = max(1, steps_per_epoch // args.evals_per_epoch)

acc, nll, wf = evaluate(val)
log(event="eval", step=0, epoch=0.0, val_acc=round(acc, 4), val_nll=round(nll, 4), per_wf=wf)
best = (acc, -nll)
best_step, since = 0, 0


def save_best():
    d = os.path.join(args.out, "best")
    model.save_pretrained(d)
    if not os.path.isdir(os.path.join(d, "tokenizer")):
        shutil.copytree(os.path.join(args.release, "tokenizer"), os.path.join(d, "tokenizer"))


save_best()
model.train()
step, stop = 0, False
for ep in range(args.epochs):
    run_loss, run_n = 0.0, 0
    for b in batches(train, args.batch, True):
        x = to_dev(collate(b))
        y = x.pop("labels")
        with torch.autocast(**AMP):
            s = model(**x)
        loss = F.cross_entropy(s.float(), y)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(params, 1.0)
        opt.step()
        sched.step()
        step += 1
        run_loss += loss.item() * len(b)
        run_n += len(b)
        if step % eval_every == 0 or step == total:
            acc, nll, wf = evaluate(val)
            improved = (acc, -nll) > best
            log(event="eval", step=step, epoch=round(step / steps_per_epoch, 2), train_loss=round(run_loss / run_n, 4),
                val_acc=round(acc, 4), val_nll=round(nll, 4), per_wf=wf, improved=improved,
                lr=opt.param_groups[0]["lr"], vram_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2) if dev.type == "cuda" else None)
            run_loss, run_n = 0.0, 0
            if improved:
                best, best_step, since = (acc, -nll), step, 0
                save_best()
            else:
                since += 1
                if since >= args.patience:
                    stop = True
                    break
    if stop:
        break

summary = {"best_val_acc": round(best[0], 4), "best_val_nll": round(-best[1], 4), "best_step": best_step,
           "best_epoch": round(best_step / steps_per_epoch, 2), "steps_run": step, "early_stopped": stop,
           "wall_s": round(time.time() - t_start, 1), "args": vars(args)}
json.dump(summary, open(os.path.join(args.out, "summary.json"), "w"), indent=2)
log(event="done", **summary)
