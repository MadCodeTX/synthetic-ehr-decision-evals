# synthetic-ehr-decision-evals

A benchmark for how reliably AI models make **administrative EHR routing decisions**. It includes the code to run
it, the synthetic data, and the results of the runs we did.

There are three tasks, each a finite-choice decision under a written policy. `needs_human_review` is always a
possible answer (the policy says when it is required):

| workflow | decision |
|---|---|
| `chart` | Which candidate chart belongs to the requested patient? (MRN, name, DOB, contact-detail matching under strict conventions) |
| `inbox` | Which work queue should this patient-portal / staff / pharmacy message go to? (18 queues; symptom, proxy, identity, multi-request and records-release rules) |
| `results` | Which review queue should this result notification go to? (critical flags, status, cosign, outside labs, specialty types) |

**All data is synthetic and fabricated. Tasks are administrative routing only: no diagnosis or treatment. This
project is not affiliated with, endorsed by, or derived from any EHR vendor's software.** Every label is computed
by code from ground-truth facts plus a frozen policy (`policy/POLICY.md`). No model labeled anything.

## Results

### Bank v2, core shard (9,350 trials per model; holdout = 2,969 cases of unseen wording and unseen case types)

<!-- V2_TABLE -->
| | **Clef-flash LoRA fine-tuned (v1 dev)** | **Clef 27B head fine-tuned (v1 dev, nf4)** | **Jev 1.13** | **Clef-flash head fine-tuned (v1 dev)** | **Qwen3.8-27B (chat JSON)** | **Clef 27B zero-shot (nf4)** | **SemIf Qwen3.8-27B** | **Laya fine-tuned (v1 dev)** | **CLM-8B fine-tuned (v1 dev)** | **Clef-flash zero-shot** | **SemIf Qwen3.5-4B** | **CLM-8B zero-shot** | **Laya zero-shot** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Holdout accuracy** | **90.5%** | **87.6%** | **83.6%** | **81.9%** | **76.2%** | **75.4%** | **73.0%** | **72.8%** | **60.4%** | **52.6%** | **50.6%** | **41.6%** | **32.0%** |
| 95% CI | 89.4–91.5 | 86.3–88.7 | 82.3–84.9 | 80.5–83.2 | 74.6–77.7 | 73.9–77.0 | 71.3–74.5 | 71.1–74.3 | 58.6–62.1 | 50.8–54.4 | 48.8–52.4 | 39.8–43.3 | 30.4–33.7 |
| chart / inbox / results | 75.4 / 98.1 / 98.5 | 73.4 / 96.5 / 93.4 | 70.2 / 97.3 / 84.3 | 72.0 / 91.2 / 83.0 | 71.1 / 88.6 / 69.5 | 69.0 / 85.3 / 72.7 | 64.8 / 88.3 / 66.7 | 63.9 / 82.1 / 72.9 | 49.4 / 71.2 / 61.2 | 53.4 / 56.4 / 48.2 | 51.8 / 58.0 / 42.3 | 51.0 / 33.1 / 40.0 | 30.8 / 40.4 / 25.3 |
| Abstains when it should | 91.9% | 91.2% | 74.8% | 81.0% | 80.9% | 56.5% | 65.0% | 80.4% | 78.2% | 15.7% | 17.2% | 72.9% | 11.7% |
| Abstains when it shouldn't | 1.4% | 8.3% | 1.9% | 11.2% | 13.4% | 4.6% | 13.8% | 27.2% | 32.2% | 3.5% | 4.0% | 46.6% | 7.6% |
| Valid output | 100.0% | 100.0% | 100.0% | 100.0% | 97.3% | 100.0% | 99.9% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Latency p50 (conc. 1 → 8) | 129 → 1072 ms | 491 → 4078 ms | 322 → 311 ms | 133 → 1176 ms | 511 → 2752 ms | 515 → 4506 ms | 915 → 5042 ms | 26 → 89 ms | 96 → 612 ms | 134 → 1166 ms | 134 → 1082 ms | 95 → 607 ms | 24 → 91 ms |
| Calibration ECE | 0.0386 | 0.0608 | 0.0156 | 0.0832 | — | 0.0914 | 0.0409 | 0.0595 | 0.1545 | 0.2961 | 0.2547 | 0.3505 | 0.2107 |
| API cost for the run | $0.00 | $0.00 | $0.55 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
<!-- /V2_TABLE -->

Holdout-only case types. No model or fine-tune saw these case types in development data:

<!-- V2_UNSEEN -->
| holdout-only case type | n | Clef-flash LoRA fine-tuned (v1 dev) | Clef 27B head fine-tuned (v1 dev, nf4) | Jev 1.13 | Clef-flash head fine-tuned (v1 dev) | Qwen3.8-27B (chat JSON) | Clef 27B zero-shot (nf4) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | CLM-8B fine-tuned (v1 dev) | Clef-flash zero-shot | SemIf Qwen3.5-4B | CLM-8B zero-shot | Laya zero-shot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `chart/two_edit_name` | 500 | 55.0 | 61.0 | 57.6 | 61.6 | 57.6 | 58.6 | 55.2 | 62.0 | 49.6 | 50.4 | 50.2 | 52.2 | 30.8 |
| `inbox/same_destination_multi_request` | 500 | 98.2 | 96.0 | 97.4 | 93.0 | 88.0 | 90.4 | 90.8 | 84.4 | 74.4 | 56.4 | 55.4 | 32.8 | 44.8 |
| `results/rule_order_collisions` | 500 | 97.0 | 91.6 | 80.6 | 82.6 | 57.8 | 69.2 | 63.2 | 69.8 | 57.8 | 45.6 | 40.0 | 38.2 | 22.6 |
<!-- /V2_UNSEEN -->

### Bank v1 (6,226 trials per model; holdout = 1,032 cases)

<!-- V1_TABLE -->
| | **Clef-flash LoRA fine-tuned (v1 dev)** | **Clef 27B head fine-tuned (v1 dev, nf4)** | **Jev 1.13** | **Clef-flash head fine-tuned (v1 dev)** | **Qwen3.8-27B (chat JSON)** | **Clef 27B zero-shot (nf4)** | **SemIf Qwen3.8-27B** | **Laya fine-tuned (v1 dev)** | **CLM-8B fine-tuned (v1 dev)** | **Clef-flash zero-shot** | **SemIf Qwen3.5-4B** | **CLM-8B zero-shot** | **Laya zero-shot** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Holdout accuracy** | **100.0%** | **96.2%** | **91.4%** | **89.8%** | **87.0%** | **79.6%** | **79.2%** | **79.1%** | **64.2%** | **53.0%** | **50.4%** | **41.5%** | **29.2%** |
| 95% CI | 99.6–100.0 | 94.9–97.2 | 89.5–92.9 | 87.8–91.5 | 84.8–88.9 | 77.0–81.9 | 76.6–81.5 | 76.5–81.4 | 61.3–67.1 | 50.0–56.0 | 47.3–53.4 | 38.5–44.5 | 26.5–32.0 |
| chart / inbox / results | 100.0 / 100.0 / 100.0 | 92.3 / 99.7 / 97.3 | 88.2 / 97.4 / 89.6 | 89.6 / 90.1 / 89.9 | 87.4 / 91.1 / 83.3 | 80.2 / 84.8 / 74.6 | 77.7 / 88.7 / 72.7 | 72.8 / 89.4 / 76.8 | 50.3 / 76.8 / 67.8 | 55.2 / 54.0 / 50.0 | 47.0 / 63.6 / 42.9 | 53.0 / 33.1 / 36.9 | 29.4 / 34.4 / 24.6 |
| Abstains when it should | 99.9% | 98.7% | 82.1% | 91.7% | 85.6% | 60.4% | 70.5% | 87.8% | 91.9% | 15.0% | 17.1% | 73.0% | 10.2% |
| Abstains when it shouldn't | 0.1% | 2.4% | 1.5% | 3.3% | 10.0% | 3.7% | 13.7% | 14.3% | 18.3% | 3.0% | 4.2% | 44.7% | 7.7% |
| Valid output | 100.0% | 100.0% | 100.0% | 100.0% | 96.8% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Latency p50 (conc. 1 → 8) | 119 → 975 ms | 457 → 3836 ms | 233 → 286 ms | 125 → 1085 ms | 484 → 2418 ms | 499 → 4112 ms | 659 → 4721 ms | 70 → 90 ms | 32 → 32 ms | 120 → 1021 ms | 174 → 986 ms | 32 → 33 ms | 70 → 92 ms |
| Calibration ECE | 0.0008 | 0.0079 | 0.016 | 0.014 | — | 0.0739 | 0.0286 | 0.0386 | 0.0183 | 0.3003 | 0.2578 | 0.3319 | 0.2393 |
| API cost for the run | $0.00 | $0.00 | $0.35 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
<!-- /V1_TABLE -->

Full per-stratum tables, metric definitions, label-quality checks and caveats are in
[`results/README.md`](results/README.md), [`results/v2/SCOREBOARD.md`](results/v2/SCOREBOARD.md) and
[`results/v1/SCOREBOARD.md`](results/v1/SCOREBOARD.md). Per-trial records are in `results/*/trials/`.

### What we learned

1. **Fine-tuned Clef is the most accurate model we have tested.** Clef and Clef-flash are Cloudflare decision models
   with a joint schema head that scores every option in one forward pass. Fine-tuned on the 4,084 v1 dev items,
   the same data Laya and CLM-8B used:
   - **Clef-flash (9B) with LoRA** scores **90.5% on the v2 holdout**, 6.9 points above Jev. It abstains when it
     should 91.9% of the time and when it shouldn't only 1.4%. It also scores 97.0% on the unseen
     `rule_order_collisions` case type (Jev: 80.6%). Training took about 100 minutes on one RTX 4090.
   - **Clef-flash, head only** (backbone frozen, about 20 GPU-minutes) goes from 52.6% zero-shot to **81.9%**.
   - **Clef 27B, head only** (4-bit backbone) goes from 75.4% zero-shot to **87.6%**.
   - Its v1 holdout scores (100.0% for Clef-flash LoRA, 96.2% for Clef 27B head-only) are in-distribution: v1
     holdout shares wording with v1 dev. The v2 holdout, with unseen wording and case types, is the honest number.
   - Chart matching remains the weak spot: 73–75% on the v2 holdout for fine-tuned Clef vs 70.2% for Jev.
     `two_edit_name` stays near chance for every model.
   - Zero-shot, Clef-flash almost never abstains (15.7% of cases where abstaining was right) and scores about 53%.
     Clef 27B zero-shot is about level with Qwen chat.
   - **Latency:** Clef-flash takes about 130 ms per decision at concurrency 1 on one RTX 4090; Clef 27B (4-bit)
     takes about 490 ms. Our server runs one request at a time with no batching, so at concurrency 8 these
     queue to about 1.1 s and 4.1 s, where Jev stays at 0.31 s.
2. **Among models used as shipped, Jev 1.13 is the most accurate on both banks**: 91.4% v1 holdout and 83.6% v2
   holdout, where the runner-up, Qwen3.8-27B chat, scored 87.0% and 76.2%. It also has the best calibration (ECE 0.016), the fewest
   unnecessary abstentions (1.9%), and 100% valid outputs. It costs about $0.06 per 1,000 decisions, with flat
   latency of about 0.3 s from concurrency 1 to 8.
3. **Its weak spot is noticing what is missing.** When a result notification lacks the patient's name or MRN,
   Jev routes it anyway: 46% correct on `incomplete_identity`, vs. 69% for Qwen chat and 81% for fine-tuned
   Laya. It also abstains less often when abstaining is correct (74.8% vs. Qwen's 80.9%).
4. **v2 is meaningfully harder.** Every capable model lost 6–11 points on the v2 holdout (unseen wording and
   unseen case types).
   - Chart matching is the hardest workflow: about 70% even for the best models.
   - No model can reliably count a two-letter name difference: no model scores above 62% on `two_edit_name`.
5. **Ordered-rule reasoning separates the models.** On `rule_order_collisions`, where two policy rules fire and
   the earlier one wins, Jev scores 80.6% and Qwen chat 57.8%.
6. **Changing the interface did not rescue Qwen.** SemIf reads the same Qwen family's answer from one forward
   pass, with no text generation. That gives 99.9–100% valid outputs and good calibration, but it was *less*
   accurate than plain chat JSON (73.0% vs 76.2% on v2) and slower on our 2-GPU EXL3 setup.
7. **Fine-tuning helps a small router a lot, but it overfits to wording.** About 7 GPU-minutes of fine-tuning on
   v1 dev took Laya from 29% to 79% on the v1 holdout. On v2's unseen wording it fell back to 72.8%, and it
   abstains on 27% of cases that had a routable answer. It is by far the fastest model (about 26 ms at
   concurrency 1).
8. **CLM-8B does not transfer to this task, and its speed advantage is smaller than advertised.**
   - **Accuracy:** zero-shot, it scores 41.6% on the v2 holdout (41.5% on v1). In 46.6% of cases where a routable
     answer existed, it chooses `needs_human_review`.
   - **Fine-tuning** on v1 dev with CLM's own trainer (recipe chosen on dev validation) raises that to 60.4% on
     v2 and 64.2% on v1. That is still 23 points behind Jev and 12 points behind the fine-tuned Laya.
   - **Latency:** one CLM decision takes about 95 ms at concurrency 1, 3.4× faster than Jev (322 ms), not the
     "up to 9×" in its announcement. With one GPU for the encoder it degrades to about 610 ms at concurrency 8,
     where Jev stays at 0.31 s. Repeated states are served from CLM's embedding cache in about 30 ms.
9. **Small and zero-shot models are not usable for this task**: SemIf Qwen3.5-4B scores about 50% and stock Laya
   about 30%, despite always producing valid options.
10. **Label quality.** In 166 v1 cases and 404 v2 cases, the three strongest models agreed on a different answer
   than the label. The independent rule oracle confirms the label in all of them, and a hand-checked sample found
   no label errors. These are shared model blind spots, not bank errors.

### The models

- **Jev 1.13** (`typesafe/jev-1.13`): a decision model called through the OpenRouter Decisions API. It returns
  a choice plus per-option probabilities.
- **Qwen3.8-27B (chat JSON)**: `Qwen/Qwen3.8-27B-FP8` on vLLM, prompted to return `{"choice": …}`; temperature 0.
- **SemIf**: the same Qwen family read out by a single forward pass over the option letters, with no text
  generation (exllamav3, EXL3 8 bpw). Its output is always a valid option, and it has native probabilities.
- **Laya**: `convaiinnovations/laya` typed-decision router, stock (zero-shot) and fine-tuned on the **v1 dev
  split only** using its official recipe.
- **CLM-8B**: [Contrastive-LM/CLM](https://github.com/Contrastive-LM/CLM). A frozen Qwen3-8B encoder
  (vLLM, last-token pooling) plus 18.9M-parameter state and action projection heads, served behind its
  TypeSafe-compatible `/v1/systemone` API. It was tested with the stock reference head (zero-shot) and with
  heads fine-tuned on the **v1 dev split only** using CLM's own `finetune.py --task choice`. See
  [`integrations/clm/`](integrations/clm/).
- **Clef / Clef-flash**: [Cloudflare/clef](https://huggingface.co/Cloudflare/clef) (Qwen3.8-27B backbone) and
  [Cloudflare/clef-flash](https://huggingface.co/Cloudflare/clef-flash) (Qwen3.5-9B backbone). Each pairs the
  backbone with a joint schema head that returns one probability per option, with no text generation. They are
  served with transformers behind their `/v1/systemone` API. Clef-flash runs in BF16 on one GPU. Clef 27B runs
  4-bit (bitsandbytes nf4) on one GPU, because BF16 needs about 55 GB. The fine-tunes use the **v1 dev split
  only**: head-only (frozen backbone) for both models, and LoRA (r=16) plus the head for Clef-flash. See
  [`integrations/clef/`](integrations/clef/).

All self-hosted models ran on one server with 2× RTX 4090. All models received byte-identical evidence, options
and policy text.

## Quick start

Requirements: Node ≥ 22.6 and Python ≥ 3.9. There are no dependencies to install.

```bash
git clone https://github.com/MadCodeTX/synthetic-ehr-decision-evals && cd synthetic-ehr-decision-evals
npm test                      # offline unit tests
npm run doctor                # environment check (offline; add -- --network to test connectivity)
cp .env.example .env          # put OPENROUTER_API_KEY (and optionally QWEN_BASE_URL / QWEN_MODEL) here

# offline rehearsal of a full run against the built-in mock
node --experimental-strip-types evals/runner.ts --dry-run --run-dir runs/dry \
  --plan data/plans/plan_jev.json --bank data/bank.jsonl --policy policy/policy.json --phases pilot,broad

# a real, small Jev run (costs well under $0.05)
node --experimental-strip-types evals/runner.ts --run-dir runs/jev-pilot \
  --plan data/plans/plan_jev.json --bank data/bank.jsonl --policy policy/policy.json --phases pilot --concurrency 4
```

**Windows (PowerShell)** works the same way; no admin rights are needed. `docs/RUNNING.md` covers portable Node,
corporate proxies (`NODE_USE_ENV_PROXY=1`), TLS-inspecting networks (`NODE_EXTRA_CA_CERTS`), and keys.

Other models:
- any OpenAI-compatible chat endpoint: `--qwen-base <url> --qwen-model <id>` on a `qwen` plan (OpenRouter works;
  cost is counted);
- any self-hosted Jev-compatible decisions service: `--decisions-base <url>` on `plan_decisions.json`;
- a direct-logit service: `--semif-base <url>`;
- a TypeSafe System One server such as CLM or Clef: `--decisions-base <url> --decisions-path /v1/systemone` with
  `DECISIONS_MODEL=<served model name>` ([`integrations/clm/`](integrations/clm/), [`integrations/clef/`](integrations/clef/)).

A dependency-free Python client lives in `simple/`.

**Bank v2:**
1. Unpack it with `python3 data/v2/unpack.py`. This verifies the sha256.
2. Use the shard plans in `data/v2/plans/`. `s00` is the 9,350-trial core shard used above; s01–s05 cover every
   remaining case.
3. Make single-model plans with `python3 data/v2/derive_plan.py <plan> --arm jev|qwen|decisions|semif --out …`.

**Verify and score a run:**
```bash
python3 verify/verify_paired.py <run_dir> --bank data/bank.jsonl --plan data/plans/plan_jev-qwen.json --policy policy/policy.json
python3 verify/verify_single.py <run_dir> --bank data/bank.jsonl --plan data/plans/plan_jev.json --policy policy/policy.json --arm jev
python3 analysis/analyze.py      <run_dir> --bank data/bank.jsonl --out metrics
```

## What's in the repo

| path | contents |
|---|---|
| `data/bank.jsonl`, `data/plans/` | **Bank v1**: 5,266 cases, 37 strata, counterfactual families, a repeat panel, and a holdout split ([docs/DATA.md](docs/DATA.md)) |
| `data/v2/` | **Bank v2**: 54,613 cases, 52 strata, an unseen-wording holdout and 3 unseen case types ([docs/DATA_V2.md](docs/DATA_V2.md)) |
| `policy/` | the frozen routing policy each model receives |
| `generator/` | deterministic generators and self-checks. `generate.py` (v1) and `v2/generate_v2.py` reproduce the published banks byte-for-byte. `v2/templates/` holds the validated template library. |
| `oracle/rules.py` | a label-blind rule implementation of the policy, used as an independent label check |
| `evals/` | the TypeScript harness: exact prompts, strict validation, budgets, resume, drift checks, and an offline mock |
| `simple/` | a minimal Python client that sends the same prompts |
| `integrations/clm/` | scripts to serve, fine-tune (v1 dev only) and benchmark CLM-8B |
| `integrations/clef/` | scripts to serve, fine-tune (v1 dev only; head-only and LoRA) and benchmark Clef / Clef-flash |
| `verify/`, `analysis/` | independent run verifiers and metrics |
| `results/` | scoreboards and per-trial records for every run above |
| `docs/` | spec, data design, and a guide to running on a locked-down work machine |

`shasum -a 256 -c SHA256SUMS` verifies every data file and the policy.

## License

Code: MIT (`LICENSE`). Data and results: CC BY 4.0 (`LICENSE-DATA.md`).
