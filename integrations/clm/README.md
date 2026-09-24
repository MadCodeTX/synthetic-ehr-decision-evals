# CLM-8B integration

How CLM-8B ([Contrastive-LM/CLM](https://github.com/Contrastive-LM/CLM), Apache-2.0) was served, fine-tuned and
benchmarked for the `CLM-8B` rows in `results/`.

CLM answers the same typed-decision wire format as Jev (`{model, state, questions: {choice: {type, instructions,
criteria}}}`) at `POST /v1/systemone`. Its answers carry `choice`, `probabilities` and `confidence`, so the
harness's strict Jev validator applies unchanged. The harness's generic `decisions` arm talks to it through
`--decisions-path /v1/systemone`.

## 1. Setup (Linux, NVIDIA GPU; two GPUs used here)

```bash
git clone https://github.com/Contrastive-LM/CLM.git
python3 -m venv clm-venv && clm-venv/bin/pip install torch fastapi uvicorn requests pyarrow transformers huggingface_hub
hf download Qwen/Qwen3-8B                                          # encoder weights (~16 GB)
hf download Contrastive-LM/CLM-v0.1-8B --local-dir clm-ckpts        # reference heads (CLM_v0.1-8B.pt, 73 MB)
```

The CLM repository itself is run from source (`PYTHONPATH=CLM/src`). Nothing from it is pip-installed.

The checkpoints are plain tensors; load them with `torch.load(..., weights_only=True)` to check.

## 2. Serve

```bash
CLM_REPO=$PWD/CLM CLM_PY=$PWD/clm-venv/bin/python CLM_CKPT=$PWD/clm-ckpts/CLM_v0.1-8B.pt \
  [CLM_FT=$PWD/clm-ft-v1dev/run/best_head.pt] ./serve_clm.sh
```

This starts:
- the encoder: vLLM `v0.30.0` in Docker, `Qwen/Qwen3-8B`, `--runner pooling` (last-token), prefix caching,
  `--max-model-len 8192`, GPU 0;
- the API: `clm.server` on `:8700`, with its heads on `cuda:1`. It serves `clm-latest` (reference head), plus
  `clm-ft-v1dev` when `CLM_FT` is set.

**Token budget.** CLM's default embedder budget is 2,048 tokens, and vLLM truncation keeps the *last* tokens. Our
longest prompts are about 3,100 tokens, so the default silently drops the start of the evidence. On 600 v1 dev
trials, 8,192 tokens scored 38.8% vs 37.2% for 2,048, at the same latency.

## 3. Fine-tune on v1 dev only

With the encoder from step 2 running:

```bash
CLM_REPO=... CLM_PY=... CLM_CKPT=... ./finetune_clm_v1dev.sh
```

`prep_clm_choice_data.py` writes the same 4,084 v1 **dev** rows the Laya fine-tune used. The rows are in the
exact wire format the harness sends (`state` = evidence, `instructions` = INSTR(policy), `criteria` = options in
presented order). 5% of dev families form the trainer's required `test` split. v1 holdout and v2 are never
read.

The recipe (`--loss softce --epochs 60 --lr 1e-3`) was chosen on the trainer's dev validation split:

| recipe | dev val acc |
|---|---|
| CLM default (InfoNCE, 20 epochs) | 59.5% |
| softce, 40 epochs | 60.3% |
| InfoNCE, 60 epochs | 68.8% |
| **softce, 60 epochs, lr 1e-3** | **70.1%** |

## 4. Benchmark

```bash
DECISIONS_MODEL=clm-latest DECISIONS_EXPECTED_MODEL=clm-latest DECISIONS_PATH=/v1/systemone \
node --experimental-strip-types evals/runner.ts --run-dir runs/clm-v1 --plan data/plans/plan_decisions.json \
  --bank data/bank.jsonl --policy policy/policy.json \
  --phases pilot,broad,counterfactual,repeat,load_c1,load_c2,load_c4,load_c8,holdout \
  --concurrency 6 --decisions-base http://<host>:8700
python3 verify/verify_single.py runs/clm-v1 --bank data/bank.jsonl --plan data/plans/plan_decisions.json \
  --policy policy/policy.json --arm decisions --checkpoints clm-latest
```

For the fine-tuned head, use `clm-ft-v1dev` as the model name. For bank v2, derive a decisions plan with
`data/v2/derive_plan.py … --arm decisions`.

Start a fresh `clm.server` for each scored run: its in-memory embedding cache would otherwise make a second run
over the same bank look faster than it is.
