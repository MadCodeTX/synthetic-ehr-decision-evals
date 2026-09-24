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
| | **Jev 1.13** | **Qwen3.8-27B (chat JSON)** | **SemIf Qwen3.8-27B** | **Laya fine-tuned (v1 dev)** | **CLM-8B fine-tuned (v1 dev)** | **SemIf Qwen3.5-4B** | **CLM-8B zero-shot** | **Laya zero-shot** |
|---|---|---|---|---|---|---|---|---|
| **Holdout accuracy** | **83.6%** | **76.2%** | **73.0%** | **72.8%** | **60.4%** | **50.6%** | **41.6%** | **32.0%** |
| 95% CI | 82.3–84.9 | 74.6–77.7 | 71.3–74.5 | 71.1–74.3 | 58.6–62.1 | 48.8–52.4 | 39.8–43.3 | 30.4–33.7 |
| chart / inbox / results | 70.2 / 97.3 / 84.3 | 71.1 / 88.6 / 69.5 | 64.8 / 88.3 / 66.7 | 63.9 / 82.1 / 72.9 | 49.4 / 71.2 / 61.2 | 51.8 / 58.0 / 42.3 | 51.0 / 33.1 / 40.0 | 30.8 / 40.4 / 25.3 |
| Abstains when it should | 74.8% | 80.9% | 65.0% | 80.4% | 78.2% | 17.2% | 72.9% | 11.7% |
| Abstains when it shouldn't | 1.9% | 13.4% | 13.8% | 27.2% | 32.2% | 4.0% | 46.6% | 7.6% |
| Valid output | 100.0% | 97.3% | 99.9% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Latency p50 (conc. 1 → 8) | 322 → 311 ms | 511 → 2752 ms | 915 → 5042 ms | 26 → 89 ms | 96 → 612 ms | 134 → 1082 ms | 95 → 607 ms | 24 → 91 ms |
| Calibration ECE | 0.0156 | — | 0.0409 | 0.0595 | 0.1545 | 0.2547 | 0.3505 | 0.2107 |
| API cost for the run | $0.55 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
<!-- /V2_TABLE -->

Holdout-only case types. No model or fine-tune saw these case types in development data:

<!-- V2_UNSEEN -->
| holdout-only case type | n | Jev 1.13 | Qwen3.8-27B (chat JSON) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | CLM-8B fine-tuned (v1 dev) | SemIf Qwen3.5-4B | CLM-8B zero-shot | Laya zero-shot |
|---|---|---|---|---|---|---|---|---|---|
| `chart/two_edit_name` | 500 | 57.6 | 57.6 | 55.2 | 62.0 | 49.6 | 50.2 | 52.2 | 30.8 |
| `inbox/same_destination_multi_request` | 500 | 97.4 | 88.0 | 90.8 | 84.4 | 74.4 | 55.4 | 32.8 | 44.8 |
| `results/rule_order_collisions` | 500 | 80.6 | 57.8 | 63.2 | 69.8 | 57.8 | 40.0 | 38.2 | 22.6 |
<!-- /V2_UNSEEN -->

### Bank v1 (6,226 trials per model; holdout = 1,032 cases)

<!-- V1_TABLE -->
| | **Jev 1.13** | **Qwen3.8-27B (chat JSON)** | **SemIf Qwen3.8-27B** | **Laya fine-tuned (v1 dev)** | **CLM-8B fine-tuned (v1 dev)** | **SemIf Qwen3.5-4B** | **CLM-8B zero-shot** | **Laya zero-shot** |
|---|---|---|---|---|---|---|---|---|
| **Holdout accuracy** | **91.4%** | **87.0%** | **79.2%** | **79.1%** | **64.2%** | **50.4%** | **41.5%** | **29.2%** |
| 95% CI | 89.5–92.9 | 84.8–88.9 | 76.6–81.5 | 76.5–81.4 | 61.3–67.1 | 47.3–53.4 | 38.5–44.5 | 26.5–32.0 |
| chart / inbox / results | 88.2 / 97.4 / 89.6 | 87.4 / 91.1 / 83.3 | 77.7 / 88.7 / 72.7 | 72.8 / 89.4 / 76.8 | 50.3 / 76.8 / 67.8 | 47.0 / 63.6 / 42.9 | 53.0 / 33.1 / 36.9 | 29.4 / 34.4 / 24.6 |
| Abstains when it should | 82.1% | 85.6% | 70.5% | 87.8% | 91.9% | 17.1% | 73.0% | 10.2% |
| Abstains when it shouldn't | 1.5% | 10.0% | 13.7% | 14.3% | 18.3% | 4.2% | 44.7% | 7.7% |
| Valid output | 100.0% | 96.8% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| Latency p50 (conc. 1 → 8) | 233 → 286 ms | 484 → 2418 ms | 659 → 4721 ms | 70 → 90 ms | 32 → 32 ms | 174 → 986 ms | 32 → 33 ms | 70 → 92 ms |
| Calibration ECE | 0.016 | — | 0.0286 | 0.0386 | 0.0183 | 0.2578 | 0.3319 | 0.2393 |
| API cost for the run | $0.35 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
<!-- /V1_TABLE -->

Full per-stratum tables, metric definitions, label-quality checks and caveats are in
[`results/README.md`](results/README.md), [`results/v2/SCOREBOARD.md`](results/v2/SCOREBOARD.md) and
[`results/v1/SCOREBOARD.md`](results/v1/SCOREBOARD.md). Per-trial records are in `results/*/trials/`.

### What we learned

1. **Jev 1.13 is the most accurate model on both banks**: 91.4% v1 holdout and 83.6% v2 holdout, where the
   runner-up, Qwen3.8-27B chat, scored 87.0% and 76.2%. It also has the best calibration (ECE 0.016), the fewest
   unnecessary abstentions (1.9%), and 100% valid outputs. It costs about $0.06 per 1,000 decisions, with flat
   latency of about 0.3 s from concurrency 1 to 8.
2. **Its weak spot is noticing what is missing.** When a result notification lacks the patient's name or MRN,
   Jev routes it anyway: 46% correct on `incomplete_identity`, vs. 69% for Qwen chat and 81% for fine-tuned
   Laya. It also abstains less often when abstaining is correct (74.8% vs. Qwen's 80.9%).
3. **v2 is meaningfully harder.** Every capable model lost 6–11 points on the v2 holdout (unseen wording and
   unseen case types).
   - Chart matching is the hardest workflow: about 70% even for the best models.
   - No model can reliably count a two-letter name difference: no model scores above 62% on `two_edit_name`.
4. **Ordered-rule reasoning separates the models.** On `rule_order_collisions`, where two policy rules fire and
   the earlier one wins, Jev scores 80.6% and Qwen chat 57.8%.
5. **Changing the interface did not rescue Qwen.** SemIf reads the same Qwen family's answer from one forward
   pass, with no text generation. That gives 99.9–100% valid outputs and good calibration, but it was *less*
   accurate than plain chat JSON (73.0% vs 76.2% on v2) and slower on our 2-GPU EXL3 setup.
6. **Fine-tuning helps a small router a lot, but it overfits to wording.** About 7 GPU-minutes of fine-tuning on
   v1 dev took Laya from 29% to 79% on the v1 holdout. On v2's unseen wording it fell back to 72.8%, and it
   abstains on 27% of cases that had a routable answer. It is by far the fastest model (about 26 ms at
   concurrency 1).
7. **CLM-8B does not transfer to this task, and its speed advantage is smaller than advertised.**
   - **Accuracy:** zero-shot, it scores 41.6% on the v2 holdout (41.5% on v1). In 46.6% of cases where a routable
     answer existed, it chooses `needs_human_review`.
   - **Fine-tuning** on v1 dev with CLM's own trainer (recipe chosen on dev validation) raises that to 60.4% on
     v2 and 64.2% on v1. That is still 23 points behind Jev and 12 points behind the fine-tuned Laya.
   - **Latency:** one CLM decision takes about 95 ms at concurrency 1, 3.4× faster than Jev (322 ms), not the
     "up to 9×" in its announcement. With one GPU for the encoder it degrades to about 610 ms at concurrency 8,
     where Jev stays at 0.31 s. Repeated states are served from CLM's embedding cache in about 30 ms.
8. **Small and zero-shot models are not usable for this task**: SemIf Qwen3.5-4B scores about 50% and stock Laya
   about 30%, despite always producing valid options.
9. **Label quality.** In 166 v1 cases and 404 v2 cases, the three strongest models agreed on a different answer
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
- a TypeSafe System One server such as CLM: `--decisions-base <url> --decisions-path /v1/systemone` with
  `DECISIONS_MODEL=<served model name>` ([`integrations/clm/`](integrations/clm/)).

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
| `verify/`, `analysis/` | independent run verifiers and metrics |
| `results/` | scoreboards and per-trial records for every run above |
| `docs/` | spec, data design, and a guide to running on a locked-down work machine |

`shasum -a 256 -c SHA256SUMS` verifies every data file and the policy.

## License

Code: MIT (`LICENSE`). Data and results: CC BY 4.0 (`LICENSE-DATA.md`).
