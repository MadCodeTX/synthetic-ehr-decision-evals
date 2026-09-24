# Bank v2 (54,613 cases): a 10× larger, harder, generalization-aware bank

v2 extends the v1 generator (`docs/DATA.md`). It uses the same frozen policy (`policy/policy.json`), the same
label evaluators, and the same wire format. v2 is about 10× larger. It adds a validated template library,
13 new strata, and holdout sets that test **unseen wording** and **unseen case types**.

| | |
|---|---|
| Cases | 54,613 = dev 42,461 + holdout 12,152 (chart 18,762 / inbox 14,877 / results 20,974), including 1,500 `no_valid_option` ambiguity cases (500 per workflow) |
| Files | `data/v2/bank_v2.jsonl.gz` → `python3 data/v2/unpack.py` gives `bank_v2.jsonl` (sha256 in `bank_v2.jsonl.sha256`) |
| Plans | `data/v2/plans/plan_v2_s00…s05_jev-qwen.json`. Each shard has ≤ 9,500 trials, because the runner admits ≤ 10,000 per launch. **s00 is the "core" shard** (9,350 trials: pilot, broad sample, counterfactual families, repeat panel, load sweep, and a 3,000-case stratified holdout). Shards s01–s05 hold every remaining case exactly once. Use `python3 data/v2/derive_plan.py … --arm jev\|qwen\|decisions\|semif` for single-arm variants with identical trial ids and order. |
| Regenerate | `python3 generator/v2/generate_v2.py --out /tmp/v2`. The output is byte-identical to the published bank (verified). |
| Self-check | `python3 generator/v2/selfcheck_v2.py` (after unpacking) |
| Oracle | `python3 oracle/rules.py --bank data/v2/bank_v2.jsonl --policy policy/policy.json --extra-vocab data/v2/vocab.txt --out /tmp/o.jsonl` |

## What changed vs v1

1. **Template library** (`generator/v2/templates/`, 1,078 templates, authored by an LLM).
   - Coverage: every inbox destination, plus symptom reports, negated symptoms, multi-intent requests,
     same-destination multi-requests, incomplete refills, third-party records releases, vague messages,
     injections, authority-conflict comments, and older thread messages.
   - Registers vary: casual, formal, terse, rambling, ESL, voice-to-text, frustrated, and elderly-formal.
   - **Labels never come from the LLM.** Each label-bearing template declares the destination it asks for. It is
     kept only if the declared label, the label-blind rule oracle, and a separate blind LLM labeler agree on
     3 randomized renderings (`generator/v2/validation/template_validation.jsonl`): 779 gold and 23 rejected.
   - Case labels are then computed by the same code evaluators as v1.
2. **Unseen-wording holdout.** Templates are split by hash into a `shared` pool (dev) and a `heldout` pool
   (≈ 30%, holdout only). Every holdout case uses only held-out templates. A model tuned on dev has never seen
   the holdout phrasing.
3. **Unseen case types (holdout-only strata)**, 500 cases each, never in dev:
   - `chart/two_edit_name`: a total name difference of exactly 2 letters means review;
   - `inbox/same_destination_multi_request`: two requests to the *same* queue route there;
   - `results/rule_order_collisions`: two rules fire, and the first in policy order wins.
4. **New strata** (dev + holdout):
   - chart `compound_traps`, `many_candidates` (12–16 options), `name_format_variants`, `mrn_format_variants`;
   - inbox `long_thread`, `roi_recipient_match`, `proxy_status_variants`, `compound_traps`;
   - results `long_prior_results`, `decoy_destination_bait`, `banner_case_variants`, `compound_traps`.
5. **Allocation by difficulty.** Existing strata received 700–1,500 cases each, weighted by how hard and how
   discriminating they were in v1. This was decided at the stratum level from v1 results; **no individual case
   was selected or filtered by any model's output.**
6. **Balance and shortcut audit.** On every audit metric, v2 is at or below v1:
   - always-abstain accuracy: chart 45.8 / inbox 39.1 / results 43.8%;
   - pick-first and pick-last baselines;
   - n-options leak (chart: 40.6 → 14.7 pts);
   - "critical" keyword baseline (37.3 → 33.6);
   - symptom keyword baseline (55.4 → 49.2);
   - label-in-text.
7. **No overlap with v1:** 0 v2 input hashes match any v1 case, and 0 duplicate input hashes occur within v2.

## Label quality evidence

- Chart and results: the independent re-derivation from rendered evidence (`selfcheck_v2.py`) agrees on 100%
  of cases, and the label-blind rule oracle agrees on 100%.
- Inbox: the rule oracle agrees on 14,371 / 14,377 non-ambiguous cases. The 6 disagreements were checked by
  hand. In every one, the generator label is correct, and the oracle's spelling normalizer failed on injected
  typo noise ("reneawl", "befroe").
- After the benchmark runs, every case where Jev, Qwen and SemIf-27B all agreed on a *different* answer was
  reviewed. See `results/README.md`.

## Known limitations (honest notes)

- The blind template labeler was a small, fast model. It rejected 23 templates, mostly correct-but-complex
  phone-back and interpreter requests.
- Before blind validation, the authoring model rewrote 341 templates that the keyword-based rule oracle could not
  parse, so that every template has an oracle-verifiable label. This makes labels verifiable, but it nudges inbox
  wording toward the oracle's vocabulary. Unique inbox bodies rose only modestly: 79.6% of cases in v1, 81.7% in
  v2.
- Result-interface inbox bodies (about 5% of inbox) keep the v1 structural text.
- Some v1 strata keep their designed label mixes, e.g. `ordinary_exact_id` is about 16% review. The 20–80% band
  applies to v2-authored strata.
- `n_options = 12` occurs in `many_candidates`, alongside v1's 2/4/8/16.
