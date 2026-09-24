# Results — bank v1 (5,266 cases; 6,226 trials per model)

> Every model received the same 6,226 trials (same cases, options and option order). Accuracy = choice == frozen label, one scored trial per case (phases pilot/broad/counterfactual/holdout, rep 0); invalid outputs count as wrong; no_valid_option ambiguity cases are excluded.
> Run on the pre-publication text of the bank (differs from data/bank.jsonl only in five result source-system names and one policy sentence); the 54 historical seed trials are omitted. Jev+Qwen paired run at concurrency 2. The fine-tuned Laya was trained on this bank's dev split, so its v1 holdout number is in-distribution. See results/README.md.

## Headline

| metric | Jev 1.13 | Qwen3.8-27B (chat JSON) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | SemIf Qwen3.5-4B | Laya zero-shot |
|---|---|---|---|---|---|---|
| Holdout accuracy % (95% CI) | 91.4 (89.5–92.9) | 87.0 (84.8–88.9) | 79.2 (76.6–81.5) | 79.1 (76.5–81.4) | 50.4 (47.3–53.4) | 29.2 (26.5–32.0) |
| Holdout n | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 |
| Dev accuracy % | 90.8 | 86.1 | 78.5 | 87.5 | 52.4 | 30.8 |
| Holdout chart / inbox / results % | 88.2 / 97.4 / 89.6 | 87.4 / 91.1 / 83.3 | 77.7 / 88.7 / 72.7 | 72.8 / 89.4 / 76.8 | 47.0 / 63.6 / 42.9 | 29.4 / 34.4 / 24.6 |
| Abstain when expected % | 82.1 | 85.6 | 70.5 | 87.8 | 17.1 | 10.2 |
| False abstain % (answer existed) | 1.5 | 10.0 | 13.7 | 14.3 | 4.2 | 7.7 |
| Counterfactual siblings acc % | 87.2 | 82.2 | 71.8 | 82.6 | 41.0 | 20.2 |
| Valid output % | 100.0 | 96.8 | 100.0 | 100.0 | 100.0 | 100.0 |
| Latency p50 / p95 ms | 312 / 1107 | 1759 / 3096 | 3535 / 4867 | 80 / 102 | 717 / 996 | 79 / 102 |
| p50 ms at concurrency 1 → 8 | 233 → 286 | 484 → 2418 | 659 → 4721 | 70 → 90 | 174 → 986 | 70 → 92 |
| Repeat panel identical across 4 reps % | 96.7 | 99.2 | 100.0 | 100.0 | 100.0 | 100.0 |
| Calibration ECE (chosen prob.) | 0.016 | — | 0.0286 | 0.0386 | 0.2578 | 0.2393 |
| Reported API cost, whole run (USD) | $0.35 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
| Trials | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 |

## Accuracy by stratum (dev + holdout, one scored trial per case)

| workflow / stratum | n | Jev 1.13 | Qwen3.8-27B (chat JSON) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | SemIf Qwen3.5-4B | Laya zero-shot |
|---|---|---|---|---|---|---|---|
| chart/authority_conflict_comment_vs_registration | 139 | 79.9 | 89.2 | 73.4 | 82.0 | 27.3 | 25.9 |
| chart/conflicting_dob_vs_id | 139 | 95.7 | 89.2 | 89.9 | 76.3 | 54.7 | 20.1 |
| chart/duplicate_name_explicit_id | 135 | 99.3 | 94.8 | 94.8 | 84.4 | 76.3 | 39.3 |
| chart/identifier_not_in_candidates | 140 | 88.6 | 95.7 | 77.9 | 95.0 | 37.9 | 29.3 |
| chart/injection_in_chart_text | 134 | 99.3 | 94.8 | 87.3 | 91.8 | 49.3 | 29.1 |
| chart/leading_zero_id | 136 | 88.2 | 98.5 | 80.9 | 93.4 | 45.6 | 18.4 |
| chart/missing_id_ambiguous | 135 | 75.6 | 83.0 | 74.1 | 77.8 | 34.1 | 27.4 |
| chart/missing_id_two_details_unique | 131 | 91.6 | 78.6 | 71.8 | 58.0 | 57.3 | 20.6 |
| chart/multi_field_required | 137 | 65.7 | 68.6 | 66.4 | 69.3 | 38.0 | 21.9 |
| chart/name_typo_id_dob_match | 141 | 83.7 | 63.1 | 58.9 | 84.4 | 39.0 | 32.6 |
| chart/near_miss_transposed_dob | 138 | 94.9 | 93.5 | 87.7 | 84.8 | 57.2 | 26.8 |
| chart/ordinary_exact_id | 144 | 97.9 | 95.8 | 86.8 | 84.0 | 84.7 | 39.6 |
| chart/swapped_identifier | 140 | 94.3 | 95.7 | 80.7 | 83.6 | 49.3 | 32.9 |
| inbox/ambiguous_text | 118 | 100.0 | 96.6 | 95.8 | 100.0 | 68.6 | 28.8 |
| inbox/authority_conflict | 143 | 97.9 | 87.4 | 86.0 | 94.4 | 70.6 | 46.2 |
| inbox/concise_vs_expanded | 120 | 97.5 | 96.7 | 91.7 | 96.7 | 55.0 | 30.0 |
| inbox/identity_mismatch | 138 | 84.8 | 75.4 | 67.4 | 85.5 | 33.3 | 24.6 |
| inbox/injection_in_message | 131 | 99.2 | 90.1 | 87.0 | 99.2 | 61.8 | 35.9 |
| inbox/missing_context | 147 | 99.3 | 95.9 | 95.9 | 93.9 | 40.1 | 24.5 |
| inbox/multi_intent | 135 | 97.8 | 88.1 | 89.6 | 92.6 | 56.3 | 26.7 |
| inbox/ordinary_single_intent | 198 | 99.0 | 96.0 | 92.4 | 97.5 | 91.9 | 60.6 |
| inbox/symptom_mention_exception | 143 | 99.3 | 84.6 | 88.8 | 97.2 | 82.5 | 27.3 |
| inbox/synonym_destinations | 131 | 98.5 | 90.8 | 90.8 | 96.2 | 64.9 | 37.4 |
| inbox/unauthorized_proxy | 140 | 93.6 | 89.3 | 87.1 | 84.3 | 52.1 | 28.6 |
| results/authority_conflict_source_vs_comment | 138 | 82.6 | 75.4 | 65.9 | 82.6 | 44.2 | 25.4 |
| results/conflicting_severity | 141 | 94.3 | 80.1 | 56.7 | 69.5 | 14.9 | 11.3 |
| results/critical_flagged_complete | 140 | 96.4 | 88.6 | 92.9 | 94.3 | 74.3 | 62.1 |
| results/critical_word_in_comment_only | 134 | 96.3 | 84.3 | 71.6 | 75.4 | 50.7 | 33.6 |
| results/incomplete_identity | 139 | 42.4 | 64.0 | 26.6 | 87.8 | 9.4 | 6.5 |
| results/injection_in_result_text | 139 | 84.9 | 78.4 | 61.9 | 84.9 | 42.4 | 30.9 |
| results/missing_severity | 142 | 91.5 | 77.5 | 56.3 | 84.5 | 12.0 | 6.3 |
| results/multi_field | 137 | 88.3 | 71.5 | 74.5 | 77.4 | 43.8 | 28.5 |
| results/narrative_note | 124 | 96.8 | 77.4 | 84.7 | 77.4 | 60.5 | 56.5 |
| results/out_of_policy_item_type | 136 | 85.3 | 99.3 | 72.1 | 97.1 | 40.4 | 4.4 |
| results/routine_discrete_complete | 140 | 97.1 | 95.7 | 87.9 | 77.1 | 77.9 | 65.7 |
| results/specialty_type_routing | 137 | 96.4 | 90.5 | 81.0 | 69.3 | 71.5 | 30.7 |
| results/status_routing | 136 | 89.0 | 76.5 | 72.8 | 91.9 | 39.7 | 19.1 |

## Runs

- **Jev 1.13**: run `2026-09-23T01-47-00Z-qwen-jev`, arm `jev`, served model(s): typesafe/jev-1.13-20260917
- **Qwen3.8-27B (chat JSON)**: run `2026-09-23T01-47-00Z-qwen-jev`, arm `qwen`, served model(s): Qwen/Qwen3.8-27B-FP8
- **SemIf Qwen3.8-27B**: run `2026-09-23T16-04-28Z-semif-27b`, arm `semif`, served model(s): qwen3.8-27b-exl3-8.0bpw
- **Laya fine-tuned (v1 dev)**: run `2026-09-23T14-44-51Z-laya-ft`, arm `laya`, served model(s): ehr-ft
- **SemIf Qwen3.5-4B**: run `2026-09-23T15-43-56Z-semif-4b`, arm `semif`, served model(s): qwen3.5-4b-exl3-8.0bpw
- **Laya zero-shot**: run `2026-09-23T13-41-27Z-laya-gpu`, arm `laya`, served model(s): english
