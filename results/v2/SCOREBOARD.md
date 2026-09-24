# Results — bank v2 core shard s00 (9,350 trials per model)

> Every model received the same 9,350 trials of data/v2/plans/plan_v2_s00_jev-qwen.json. Holdout = 3,000 stratified holdout trials (2,969 scored; ambiguity cases excluded): held-out wording no model has seen, plus the three holdout-only case types. Accuracy = choice == frozen label, one scored trial per case; invalid outputs count as wrong.
> All runs at concurrency 6; load phases fix concurrency at 1/2/4/8. SemIf Qwen3.8-27B: the first service instance ran out of GPU memory after 377 trials (8 trials recorded as connection errors and kept as invalid); the run was resumed on a relaunched service with a less aggressive GPU split. CLM caches state embeddings, so its load-phase latency (cases already seen earlier in the run) is much lower than its first-sight latency. See results/README.md.

## Headline

| metric | Jev 1.13 | Qwen3.8-27B (chat JSON) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | CLM-8B fine-tuned (v1 dev) | SemIf Qwen3.5-4B | CLM-8B zero-shot | Laya zero-shot |
|---|---|---|---|---|---|---|---|---|
| Holdout accuracy % (95% CI) | 83.6 (82.3–84.9) | 76.2 (74.6–77.7) | 73.0 (71.3–74.5) | 72.8 (71.1–74.3) | 60.4 (58.6–62.1) | 50.6 (48.8–52.4) | 41.6 (39.8–43.3) | 32.0 (30.4–33.7) |
| Holdout n | 2969 | 2969 | 2969 | 2969 | 2969 | 2969 | 2969 | 2969 |
| Dev accuracy % | 89.0 | 85.8 | 77.3 | 75.5 | 60.6 | 54.5 | 41.8 | 32.9 |
| Holdout chart / inbox / results % | 70.2 / 97.3 / 84.3 | 71.1 / 88.6 / 69.5 | 64.8 / 88.3 / 66.7 | 63.9 / 82.1 / 72.9 | 49.4 / 71.2 / 61.2 | 51.8 / 58.0 / 42.3 | 51.0 / 33.1 / 40.0 | 30.8 / 40.4 / 25.3 |
| Abstain when expected % | 74.8 | 80.9 | 65.0 | 80.4 | 78.2 | 17.2 | 72.9 | 11.7 |
| False abstain % (answer existed) | 1.9 | 13.4 | 13.8 | 27.2 | 32.2 | 4.0 | 46.6 | 7.6 |
| Counterfactual siblings acc % | 87.5 | 81.1 | 70.8 | 77.2 | 66.8 | 43.1 | 53.2 | 21.4 |
| Valid output % | 100.0 | 97.3 | 99.9 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| Latency p50 / p95 ms | 296 / 510 | 1920 / 3166 | 3648 / 5013 | 70 / 105 | 413 / 927 | 715 / 1215 | 414 / 931 | 66 / 83 |
| p50 ms at concurrency 1 → 8 | 322 → 311 | 511 → 2752 | 915 → 5042 | 26 → 89 | 96 → 612 | 134 → 1082 | 95 → 607 | 24 → 91 |
| Repeat panel identical across 4 reps % | 97.5 | 99.2 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| Calibration ECE (chosen prob.) | 0.0156 | — | 0.0409 | 0.0595 | 0.1545 | 0.2547 | 0.3505 | 0.2107 |
| Reported API cost, whole run (USD) | $0.55 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
| Trials | 9350 | 9350 | 9350 | 9350 | 9350 | 9350 | 9350 | 9350 |

## Accuracy by stratum (dev + holdout, one scored trial per case)

| workflow / stratum | n | Jev 1.13 | Qwen3.8-27B (chat JSON) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | CLM-8B fine-tuned (v1 dev) | SemIf Qwen3.5-4B | CLM-8B zero-shot | Laya zero-shot |
|---|---|---|---|---|---|---|---|---|---|
| chart/authority_conflict_comment_vs_registration | 123 | 79.7 | 87.0 | 78.9 | 66.7 | 52.8 | 54.5 | 52.0 | 35.0 |
| chart/compound_traps | 112 | 84.8 | 97.3 | 80.4 | 76.8 | 64.3 | 56.2 | 59.8 | 37.5 |
| chart/conflicting_dob_vs_id | 155 | 94.8 | 89.0 | 93.5 | 73.5 | 65.2 | 54.2 | 71.0 | 29.0 |
| chart/duplicate_name_explicit_id | 148 | 98.6 | 94.6 | 86.5 | 75.7 | 16.2 | 76.4 | 20.9 | 35.1 |
| chart/identifier_not_in_candidates | 119 | 89.1 | 98.3 | 78.2 | 95.0 | 70.6 | 46.2 | 73.9 | 36.1 |
| chart/injection_in_chart_text | 126 | 99.2 | 95.2 | 86.5 | 73.8 | 46.8 | 55.6 | 53.2 | 32.5 |
| chart/leading_zero_id | 129 | 82.9 | 98.4 | 76.7 | 88.4 | 64.3 | 57.4 | 51.9 | 35.7 |
| chart/many_candidates | 140 | 77.1 | 87.9 | 75.7 | 58.6 | 45.7 | 41.4 | 49.3 | 17.1 |
| chart/missing_id_ambiguous | 140 | 57.9 | 76.4 | 65.0 | 65.0 | 60.7 | 36.4 | 66.4 | 19.3 |
| chart/missing_id_two_details_unique | 177 | 81.4 | 75.7 | 66.1 | 41.8 | 19.8 | 62.1 | 21.5 | 20.9 |
| chart/mrn_format_variants | 115 | 71.3 | 73.9 | 66.1 | 80.0 | 56.5 | 46.1 | 56.5 | 35.7 |
| chart/multi_field_required | 160 | 58.1 | 69.4 | 54.4 | 48.8 | 36.2 | 49.4 | 38.8 | 40.6 |
| chart/name_format_variants | 109 | 69.7 | 78.9 | 62.4 | 58.7 | 38.5 | 59.6 | 45.0 | 26.6 |
| chart/name_typo_id_dob_match | 183 | 86.3 | 59.6 | 68.9 | 72.1 | 42.6 | 44.3 | 44.3 | 30.6 |
| chart/near_miss_transposed_dob | 121 | 97.5 | 92.6 | 84.3 | 70.2 | 49.6 | 65.3 | 45.5 | 36.4 |
| chart/ordinary_exact_id | 132 | 97.7 | 100.0 | 89.4 | 81.8 | 35.6 | 83.3 | 22.7 | 45.5 |
| chart/swapped_identifier | 134 | 88.1 | 95.5 | 71.6 | 76.1 | 67.9 | 47.8 | 71.6 | 22.4 |
| chart/two_edit_name | 500 | 57.6 | 57.6 | 55.2 | 62.0 | 49.6 | 50.2 | 52.2 | 30.8 |
| inbox/ambiguous_text | 103 | 99.0 | 97.1 | 95.1 | 91.3 | 76.7 | 70.9 | 43.7 | 27.2 |
| inbox/authority_conflict | 132 | 99.2 | 93.2 | 89.4 | 84.1 | 57.6 | 76.5 | 28.0 | 56.1 |
| inbox/compound_traps | 126 | 96.0 | 89.7 | 74.6 | 66.7 | 54.0 | 67.5 | 42.9 | 36.5 |
| inbox/concise_vs_expanded | 104 | 100.0 | 97.1 | 91.3 | 77.9 | 74.0 | 65.4 | 32.7 | 46.2 |
| inbox/identity_mismatch | 159 | 85.5 | 66.7 | 68.6 | 75.5 | 70.4 | 36.5 | 47.8 | 30.2 |
| inbox/injection_in_message | 110 | 98.2 | 92.7 | 86.4 | 80.9 | 66.4 | 60.0 | 34.5 | 31.8 |
| inbox/long_thread | 135 | 99.3 | 93.3 | 91.1 | 75.6 | 66.7 | 69.6 | 39.3 | 45.2 |
| inbox/missing_context | 107 | 99.1 | 95.3 | 88.8 | 72.0 | 64.5 | 45.8 | 38.3 | 25.2 |
| inbox/multi_intent | 116 | 98.3 | 87.9 | 87.1 | 84.5 | 75.0 | 61.2 | 33.6 | 34.5 |
| inbox/ordinary_single_intent | 212 | 96.2 | 92.0 | 86.8 | 80.2 | 60.8 | 91.0 | 24.1 | 60.4 |
| inbox/proxy_status_variants | 116 | 93.1 | 94.0 | 88.8 | 80.2 | 67.2 | 57.8 | 29.3 | 31.0 |
| inbox/roi_recipient_match | 107 | 100.0 | 95.3 | 93.5 | 72.9 | 52.3 | 50.5 | 26.2 | 24.3 |
| inbox/same_destination_multi_request | 500 | 97.4 | 88.0 | 90.8 | 84.4 | 74.4 | 55.4 | 32.8 | 44.8 |
| inbox/symptom_mention_exception | 142 | 99.3 | 78.9 | 85.9 | 82.4 | 82.4 | 78.9 | 40.8 | 36.6 |
| inbox/synonym_destinations | 110 | 98.2 | 90.0 | 85.5 | 82.7 | 73.6 | 58.2 | 44.5 | 44.5 |
| inbox/unauthorized_proxy | 122 | 91.8 | 93.4 | 85.2 | 77.0 | 65.6 | 50.0 | 39.3 | 20.5 |
| results/authority_conflict_source_vs_comment | 153 | 82.4 | 85.0 | 68.0 | 77.1 | 58.2 | 41.2 | 30.1 | 24.2 |
| results/banner_case_variants | 109 | 99.1 | 84.4 | 77.1 | 84.4 | 73.4 | 41.3 | 45.9 | 31.2 |
| results/compound_traps | 95 | 90.5 | 84.2 | 69.5 | 83.2 | 58.9 | 42.1 | 38.9 | 23.2 |
| results/conflicting_severity | 172 | 93.0 | 75.0 | 64.5 | 63.4 | 68.6 | 15.1 | 61.0 | 11.0 |
| results/critical_flagged_complete | 120 | 98.3 | 90.8 | 92.5 | 95.0 | 91.7 | 78.3 | 30.8 | 60.0 |
| results/critical_word_in_comment_only | 157 | 91.1 | 78.3 | 75.8 | 68.2 | 67.5 | 48.4 | 31.8 | 28.7 |
| results/decoy_destination_bait | 141 | 94.3 | 89.4 | 87.2 | 70.2 | 62.4 | 68.1 | 30.5 | 46.1 |
| results/incomplete_identity | 181 | 46.4 | 69.1 | 32.6 | 80.7 | 63.0 | 13.3 | 55.8 | 8.8 |
| results/injection_in_result_text | 136 | 84.6 | 82.4 | 61.8 | 77.2 | 69.9 | 34.6 | 49.3 | 26.5 |
| results/long_prior_results | 134 | 88.1 | 88.1 | 65.7 | 78.4 | 47.8 | 45.5 | 41.8 | 29.1 |
| results/missing_severity | 147 | 95.9 | 79.6 | 59.2 | 80.3 | 87.8 | 20.4 | 54.4 | 13.6 |
| results/multi_field | 157 | 88.5 | 59.9 | 80.3 | 77.1 | 59.9 | 42.0 | 32.5 | 30.6 |
| results/narrative_note | 157 | 94.3 | 78.3 | 78.3 | 69.4 | 71.3 | 61.1 | 20.4 | 50.3 |
| results/out_of_policy_item_type | 137 | 90.5 | 100.0 | 82.5 | 94.2 | 88.3 | 45.3 | 64.2 | 8.0 |
| results/routine_discrete_complete | 158 | 94.9 | 91.1 | 86.7 | 70.3 | 70.3 | 70.3 | 32.9 | 65.8 |
| results/rule_order_collisions | 500 | 80.6 | 57.8 | 63.2 | 69.8 | 57.8 | 40.0 | 38.2 | 22.6 |
| results/specialty_type_routing | 182 | 98.4 | 94.0 | 83.0 | 59.9 | 62.6 | 76.4 | 26.9 | 40.1 |
| results/status_routing | 149 | 87.2 | 78.5 | 69.8 | 95.3 | 49.7 | 40.9 | 32.2 | 11.4 |

## Runs

- **Jev 1.13**: run `v2-s00-jev-qwen`, arm `jev`, served model(s): typesafe/jev-1.13-20260917
- **Qwen3.8-27B (chat JSON)**: run `v2-s00-jev-qwen`, arm `qwen`, served model(s): Qwen/Qwen3.8-27B-FP8
- **SemIf Qwen3.8-27B**: run `v2-s00-semif-27b`, arm `semif`, served model(s): qwen3.8-27b-exl3-8.0bpw
- **Laya fine-tuned (v1 dev)**: run `v2-s00-laya-ft`, arm `decisions`, served model(s): ehr-ft
- **CLM-8B fine-tuned (v1 dev)**: run `v2-s00-clm-ft`, arm `decisions`, served model(s): clm-ft-v1dev
- **SemIf Qwen3.5-4B**: run `v2-s00-semif-4b`, arm `semif`, served model(s): qwen3.5-4b-exl3-8.0bpw
- **CLM-8B zero-shot**: run `v2-s00-clm-zeroshot`, arm `decisions`, served model(s): clm-latest
- **Laya zero-shot**: run `v2-s00-laya-zeroshot`, arm `decisions`, served model(s): english
