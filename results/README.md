# Results

Scores from the runs we did, on the banks in `data/`. Each folder has:

- `SCOREBOARD.md`: headline metrics and per-stratum accuracy for every arm;
- `scoreboard.json`: the same numbers, machine-readable;
- `trials/<arm>.jsonl.gz`: one line per trial (choice, validity, correctness, probabilities, latency, cost).
  This is enough to recompute every number or run new analyses.

| folder | bank | trials per arm | arms |
|---|---|---|---|
| [`v2/`](v2/SCOREBOARD.md) | v2 core shard `s00` (9,350 trials: 3,000-case stratified holdout with unseen wording + 3 unseen case types, dev sample, counterfactual families, repeat panel, load sweep) | 9,350 | Jev 1.13, Qwen3.8-27B, SemIf (Qwen3.8-27B, Qwen3.5-4B), Laya and CLM-8B (each zero-shot and fine-tuned on v1 dev) |
| [`v1/`](v1/SCOREBOARD.md) | v1 (5,266 cases) | 6,226 | same eight arms |

## The arms

| arm | what it is | how it was served |
|---|---|---|
| **Jev 1.13** | `typesafe/jev-1.13`, a decision model that returns a choice with per-option probabilities | OpenRouter Decisions API |
| **Qwen3.8-27B (chat JSON)** | `Qwen/Qwen3.8-27B-FP8`, asked to answer `{"choice": …}` in JSON; temperature 0 | vLLM on 2× RTX 4090, OpenAI-compatible API |
| **SemIf Qwen3.8-27B / Qwen3.5-4B** | the same Qwen weights (EXL3 8 bpw) read out by a single forward pass over the option letters (SemIf "direct-options-v1"): no generation, so always a valid option, with native probabilities | exllamav3 behind a Jev-compatible endpoint |
| **Laya zero-shot** | `convaiinnovations/laya` typed-decision router, stock checkpoints | local GPU service, Jev-compatible endpoint |
| **Laya fine-tuned** | Laya fine-tuned with its official recipe on the **v1 dev split only** (4,084 items, ~7 GPU-minutes) | same |
| **CLM-8B zero-shot** | `Contrastive-LM/CLM` v0.1: frozen `Qwen/Qwen3-8B` encoder (last-token pooling) + the reference projection heads `CLM_v0.1-8B.pt` (18.9M params); an option's score is the scaled cosine between the projected state (evidence + instructions) and the projected option description | vLLM v0.30.0 pooling server on 1× RTX 4090 + `clm.server` (`/v1/systemone`), heads on the second GPU |
| **CLM-8B fine-tuned** | the same encoder with projection heads fine-tuned by CLM's `train/finetune.py --task choice` on the **v1 dev split only** (the same 4,084 items as Laya; ~8 s of head training once embeddings are cached) | same |

All arms receive byte-identical inputs: the same evidence JSON, the same option keys and descriptions in the same
order, and the same policy text. The request format depends only on the interface: the Jev/decisions wire format,
or the chat prompt in `docs/SPEC.md` §5. `input_hash` is identical across arms for every trial.

## How to read the numbers

- **Accuracy** means choice == the frozen label. It uses one scored trial per case (phases pilot / broad /
  counterfactual / holdout, rep 0). An invalid output is wrong. The `no_valid_option` ambiguity cases, where no
  offered option is correct, are excluded from accuracy.
- **Holdout** is the headline.
  - In v2, holdout cases use only held-out templates (wording no arm has seen, including the v1-fine-tuned Laya)
    and include the three holdout-only case types.
  - In v1, the holdout shares wording with dev. The fine-tuned Laya was trained on v1 dev, so its v1 holdout
    number measures only in-distribution generalization.
- **Abstain when expected** is recall on cases whose correct answer is `needs_human_review`. **False abstain** is
  the rate of choosing `needs_human_review` when a routable answer existed.
- **Latency** is client-observed end-to-end time, including network.
  - Jev is a remote API. All other arms ran on one local server (2× RTX 4090).
  - Jev and Qwen trials were interleaved in one paired run: v1 at concurrency 2, v2 at concurrency 6.
  - Single-arm runs used the concurrency their `manifest.json` records (6 in v2).
  - The `load_c1…c8` phases fix concurrency at 1/2/4/8 for every arm, so "p50 at concurrency 1 → 8" is the
    comparable latency figure.
- **Cost**: Jev's is OpenRouter's reported `usage.cost`. Self-hosted arms have no API fee; their hardware and
  energy cost was not measured.
- **ECE** is the expected calibration error of the chosen option's probability, for arms that return
  probabilities.
- 95% CIs are Wilson intervals.

## Label quality checks behind these scores

- Labels are computed by code from ground-truth facts plus the frozen policy. No model labeled anything.
- Chart and results labels are independently re-derived from the rendered evidence (100% agreement). A separate,
  label-blind rule implementation agrees on 100% of v1 and 99.99% of v2 (6 inbox typo-noise cases, all checked
  by hand; the labels are correct).
- **Unanimous-disagreement review.** Where Jev, Qwen and SemIf-27B all chose the *same* answer that differs from
  the label, the label is the prime suspect. There are 166 such cases in v1 (of 5,116 scored) and 404 in v2
  (of 8,209 scored):
  - every one of them agrees with the label-blind rule oracle;
  - a hand-adjudicated stratified sample against the written policy (7 in v1, 10 in v2, covering the largest
    clusters and every new v2 case type) found **0 label errors**.

  The clusters are shared model blind spots:
  - missing identity fields in results notifications (rule 2);
  - a "matching" chart whose supplied address differs (chart rule 2);
  - MRNs that appear only in free-text comments;
  - one-letter name typos with MRN + DOB match, which the policy says to select;
  - in v2, two-letter name differences (policy: review), MRNs with an internal dash (not a character-for-character
    match), and rule-order collisions such as a missing status on an outside result (the status rule comes
    first).

## Provenance and caveats

- **Pre-publication text (v1 only).** The v1 runs were executed on the bank before publication scrubbing. It
  differs from `data/bank.jsonl` in exactly five result `source_system` strings (product names replaced by
  generic "Lab/Microbiology/Pathology/Cardiology/Imaging interface") and in one policy sentence (a
  product-specific name for the inbox was replaced by "inbox"). Every other byte is identical; this was checked by applying the scrub to the
  original bank and comparing byte-for-byte. The 54 historical seed trials are omitted. v2 runs used the
  published text exactly.
- **Qwen served model:** vLLM `Qwen/Qwen3.8-27B-FP8`, pinned and drift-checked every minute. SemIf used
  community EXL3 8-bpw quantizations of the same model families, so it is a system-level comparison, not a
  pure ablation of the interface.
- **Laya fine-tune:** trained only on v1 dev. v1 holdout and all of v2 were never used in training.
- **Jev prompt_hash erratum (v2 runs only):** in the v2 run records, `prompt_hash` for the Jev/decisions/SemIf
  arms was computed over the request body *including* the `model` field. The v1 runs used the earlier harness,
  which was not affected. The spec says to exclude it; the harness was fixed after these runs and a
  regression test was added. The field is metadata only: request bodies, and therefore
  results, are unaffected. Within each run the hash is consistent.
- **SemIf Qwen3.8-27B, v2:** the first service instance (GPU split 23 GB / 23 GB, the same as in v1) ran out of
  GPU memory after 377 trials. 8 in-flight trials were recorded as connection errors and are kept as invalid
  answers. The service was relaunched with a 19 GB / 23 GB split, and the run resumed without repeating any
  completed trial (2 segments in `segments.jsonl`).
- **CLM configuration and tuning (dev data only).**
  - Settings compared on 600 v1 **dev** trials:
    - embedder token budget 8,192 (38.8%) vs. CLM's default 2,048 (37.2%). vLLM truncation keeps the *last*
      tokens, so 2,048 drops the start of long evidence;
    - reference head (38.8%) vs. the no-head `clm-raw` ablation (28.0%).

    The chosen setting is 8,192 tokens with the reference head.
  - Fine-tuning recipes compared on the trainer's dev validation split (dev families only):
    - default InfoNCE, 20 epochs: 59.5%;
    - softce, 40 epochs: 60.3%;
    - InfoNCE, 60 epochs: 68.8%;
    - softce, 60 epochs at lr 1e-3: **70.1%**, chosen.

    A second, longer sweep round was not run.
  - The reference checkpoint and the fine-tuned head were loaded with `torch.load(weights_only=True)`, i.e.
    plain tensors.
  - v1 holdout and all of v2 were never used for any of these choices.
- **CLM latency.** Each scored CLM run started with a fresh `clm.server` instance, so no run inherited another
  run's embedding cache.
  - Within a run, CLM caches state embeddings. Cases seen earlier in the same run (the v1 load phases reuse dev
    cases from the broad phase) answer in about 30 ms. The v2 load phases were mostly first-sight: 95 ms at
    concurrency 1 and 607 ms at concurrency 8, with the single-GPU encoder saturating.
  - CLM runs used the published bank and policy text directly.
- Every run passed its verifier (`verify/verify_paired.py` or `verify/verify_single.py`). The verifier recomputes
  hashes, validity, correctness and coverage from the raw run records.
