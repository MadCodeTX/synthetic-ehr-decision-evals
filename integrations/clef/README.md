# Clef / Clef-flash integration

How Cloudflare's Clef decision models were served, fine-tuned and benchmarked for the `Clef` rows in `results/`:
- [Cloudflare/clef](https://huggingface.co/Cloudflare/clef): Qwen3.8-27B backbone, Apache-2.0;
- [Cloudflare/clef-flash](https://huggingface.co/Cloudflare/clef-flash): Qwen3.5-9B backbone, Apache-2.0.

A Clef release is a standard `Qwen3_5ForConditionalGeneration` backbone plus a small **joint schema head**
(`joint_head.safetensors`, about 250 MB). The head reads the backbone's final hidden states and returns one logit
per allowed option, so there is no text generation and nothing to parse. The release's own `joint_schema_model.py`
answers a Jev/SystemOne `POST /v1/systemone` body. The harness's generic `decisions` arm talks to it through
`--decisions-path /v1/systemone`.

**vLLM cannot serve the head.** It would load the backbone as a chat model and ignore the head, which is what the
benchmark measures. Everything here uses plain transformers.

## 1. Setup (Linux, NVIDIA GPU; 2× RTX 4090 used here)

```bash
python3 -m venv clef-venv
clef-venv/bin/pip install torch transformers accelerate bitsandbytes peft safetensors flash-linear-attention pillow torchvision
hf download Cloudflare/clef-flash --local-dir clef-flash     # ~19 GB
hf download Cloudflare/clef --local-dir clef                 # ~55 GB
```

Tested with torch 2.14 and transformers 5.17.

- `flash-linear-attention` supplies fast kernels for the backbone's linear-attention layers. Without them those
  layers fall back to a much slower PyTorch path.
- `causal-conv1d` is optional. Without it, transformers uses its PyTorch fallback.

## 2. Serve

```bash
CUDA_VISIBLE_DEVICES=0 CLEF_MODEL_DIR=$PWD/clef-flash CLEF_MODEL_NAME=clef-flash CLEF_QUANT=bf16 \
  clef-venv/bin/python serve_clef.py            # :8096, GET /health, POST /v1/systemone
```

| env | meaning |
|---|---|
| `CLEF_QUANT` | `bf16`, `int8` or `nf4` (bitsandbytes). `lm_head` and the vision tower always stay BF16, because the head reads the output-embedding matrix. |
| `CLEF_DEVICE_MAP` / `CLEF_MAX_MEMORY` | `cuda:0` (default), or `auto` with e.g. `0:22GiB,1:22GiB` to split across GPUs |
| `CLEF_HEAD_PATH` | a fine-tuned `joint_head.safetensors` |
| `CLEF_LORA_DIR` | a PEFT adapter for the backbone, merged into the BF16 weights at load time |

The server encodes with the release's `encode_record` / `systemone`, so its requests are byte-identical to the
reference implementation. It runs one request at a time.

**Memory**
- **Clef-flash BF16**: about 18 GB on one 24 GB GPU. Our longest prompt is about 3,000 tokens.
- **Clef 27B BF16**: about 55 GB, which does not fit in 2× 24 GB. We served it **nf4** on one GPU, at about 18 GB.
  The model card reports BF16 results only, so our Clef 27B numbers may understate the model.

## 3. Fine-tune on v1 dev only

Everything trains on the same 4,084 v1 **dev** items as the Laya and CLM-8B fine-tunes. v1 holdout and v2 are never
read.

```bash
node --experimental-strip-types build_bodies.ts <repo> harness_bodies_v1dev.jsonl
# <data_dir> must hold bank.jsonl, policy.json, harness_bodies_v1dev.jsonl,
# and simple/ehr_eval/prompts.py (used for the independent rebuild)
python prep_clef_data.py <model_dir> <data_dir> <prep_dir>
CUDA_VISIBLE_DEVICES=0 python cache_hidden.py <model_dir> <prep_dir>/records.jsonl <cache_dir> [bf16|nf4]
CUDA_VISIBLE_DEVICES=0 python train_head.py --model-dir <model_dir> --cache <cache_dir> --out ft-head --lr 3e-5
CUDA_VISIBLE_DEVICES=0 python train_lora.py --model-dir <model_dir> --cache <cache_dir> --out ft-lora   # optional
```

What each step does:
1. **Build request bodies.** `build_bodies.ts` writes the exact request bodies the harness sends, using the
   harness's own `decisionsBodyText`.
2. **Prepare records.** `prep_clef_data.py` rebuilds every body independently in Python and checks that both
   versions encode to the same token ids, spans and option ids. The 4,084 items matched with 0 differences.
   It then makes a family-grouped, stratified split: 3,652 train and 432 val.
3. **Cache hidden states.** `cache_hidden.py` runs the frozen backbone once and saves its hidden states to disk:
   34 GB for Clef-flash, 42.5 GB for Clef 27B nf4. Run through this cache, the released head reproduces the live
   logits to within 5e-6.
   - Cache with the **same quantization you will serve with**. The 27B head was trained on nf4 hidden states.
4. **Head-only training.** `train_head.py` trains the joint head from the released weights with AdamW (fp32
   master weights) and early stopping on val.
5. **LoRA (optional).** `train_lora.py` trains r=16 LoRA on the attention, linear-attention and MLP projections
   plus the head, with gradient checkpointing and batch size 1.
   - This fits one 24 GB GPU for Clef-flash in BF16.
   - LoRA for the 27B was not run (it would need nf4 QLoRA, about 2.5 h per epoch on one 4090).

Validation accuracy (432 dev items). These numbers are optimistic, because val shares generator templates with
train and was used to pick the learning rate:

| model | zero-shot | head-only | LoRA + head |
|---|---|---|---|
| Clef-flash | 54.6% | 88.0% (lr 3e-5; 1e-5: 81.3%, 1e-4: 87.3%) | 99.8% |
| Clef 27B (nf4) | 81.0% | 96.5% | — |

Wall time on one RTX 4090:
- **Clef-flash head-only:** about 20 minutes (9 min caching + 11 min for the three-LR sweep run in parallel).
- **Clef-flash LoRA:** about 100 minutes.
- **Clef 27B head-only:** about 43 minutes (33.5 min caching + 9 min sweep).

## 4. Benchmark

```bash
DECISIONS_MODEL=clef-flash DECISIONS_EXPECTED_MODEL=clef-flash DECISIONS_PATH=/v1/systemone \
node --experimental-strip-types evals/runner.ts --run-dir runs/clef-flash-v1 --plan data/plans/plan_decisions.json \
  --bank data/bank.jsonl --policy policy/policy.json \
  --phases pilot,broad,counterfactual,repeat,load_c1,load_c2,load_c4,load_c8,holdout \
  --concurrency 6 --decisions-base http://<host>:8096
python3 verify/verify_single.py runs/clef-flash-v1 --bank data/bank.jsonl --plan data/plans/plan_decisions.json \
  --policy policy/policy.json --arm decisions --checkpoints clef-flash
```

For bank v2, derive a decisions plan with `data/v2/derive_plan.py … --arm decisions`.

All ten Clef runs (5 arms × 2 banks) completed every planned trial, and `verify_single.py` passed on all of them.

## Results (holdout accuracy)

| arm | v1 | v2 (unseen wording + case types) |
|---|---|---|
| Clef-flash zero-shot | 53.0% | 52.6% |
| Clef-flash head fine-tuned | 89.8% | 81.9% |
| Clef-flash LoRA fine-tuned | 100.0% | **90.5%** |
| Clef 27B zero-shot (nf4) | 79.6% | 75.4% |
| Clef 27B head fine-tuned (nf4) | 96.2% | 87.6% |
| *Jev 1.13, for reference* | *91.4%* | *83.6%* |

The v1 holdout shares wording with v1 dev, so the fine-tuned v1 numbers are in-distribution. Use the v2 column.
See the top-level README and `results/v2/SCOREBOARD.md` for the full metrics.
