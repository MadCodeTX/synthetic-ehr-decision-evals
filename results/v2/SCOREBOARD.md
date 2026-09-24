# Results — bank v2 core shard s00 (9,350 trials per model)

> Every model received the same 9,350 trials of data/v2/plans/plan_v2_s00_jev-qwen.json. Holdout = 3,000 stratified holdout trials (2,969 scored; ambiguity cases excluded): held-out wording no model has seen, plus the three holdout-only case types. Accuracy = choice == frozen label, one scored trial per case; invalid outputs count as wrong.
> Jev+Qwen paired at concurrency 6; single-model runs at concurrency 6; load phases fix concurrency at 1/2/4/8. SemIf Qwen3.8-27B: the first service instance ran out of GPU memory after 377 trials (8 trials recorded as connection errors and kept as invalid); the run was resumed on a relaunched service with a less aggressive GPU split. See results/README.md.

## Headline

| metric | Jev 1.13 | Qwen3.8-27B (chat JSON) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | SemIf Qwen3.5-4B | Laya zero-shot |
|---|---|---|---|---|---|---|
| Holdout accuracy % (95% CI) | 83.6 (82.3–84.9) | 76.2 (74.6–77.7) | 73.0 (71.3–74.5) | 72.8 (71.1–74.3) | 50.6 (48.8–52.4) | 32.0 (30.4–33.7) |
| Holdout n | 2969 | 2969 | 2969 | 2969 | 2969 | 2969 |
| Dev accuracy % | 89.0 | 85.8 | 77.3 | 75.5 | 54.5 | 32.9 |
| Holdout chart / inbox / results % | 70.2 / 97.3 / 84.3 | 71.1 / 88.6 / 69.5 | 64.8 / 88.3 / 66.7 | 63.9 / 82.1 / 72.9 | 51.8 / 58.0 / 42.3 | 30.8 / 40.4 / 25.3 |
| Abstain when expected % | 74.8 | 80.9 | 65.0 | 80.4 | 17.2 | 11.7 |
| False abstain % (answer existed) | 1.9 | 13.4 | 13.8 | 27.2 | 4.0 | 7.6 |
| Counterfactual siblings acc % | 87.5 | 81.1 | 70.8 | 77.2 | 43.1 | 21.4 |
| Valid output % | 100.0 | 97.3 | 99.9 | 100.0 | 100.0 | 100.0 |
| Latency p50 / p95 ms | 296 / 510 | 1920 / 3166 | 3648 / 5013 | 70 / 105 | 715 / 1215 | 66 / 83 |
| p50 ms at concurrency 1 → 8 | 322 → 311 | 511 → 2752 | 915 → 5042 | 26 → 89 | 134 → 1082 | 24 → 91 |
| Repeat panel identical across 4 reps % | 97.5 | 99.2 | 100.0 | 100.0 | 100.0 | 100.0 |
| Calibration ECE (chosen prob.) | 0.0156 | — | 0.0409 | 0.0595 | 0.2547 | 0.2107 |
| Reported API cost, whole run (USD) | $0.55 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
| Trials | 9350 | 9350 | 9350 | 9350 | 9350 | 9350 |

## Accuracy by stratum (dev + holdout, one scored trial per case)

| workflow / stratum | n | Jev 1.13 | Qwen3.8-27B (chat JSON) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | SemIf Qwen3.5-4B | Laya zero-shot |
|---|---|---|---|---|---|---|---|
| chart/authority_conflict_comment_vs_registration | 123 | 79.7 | 87.0 | 78.9 | 66.7 | 54.5 | 35.0 |
| chart/compound_traps | 112 | 84.8 | 97.3 | 80.4 | 76.8 | 56.2 | 37.5 |
| chart/conflicting_dob_vs_id | 155 | 94.8 | 89.0 | 93.5 | 73.5 | 54.2 | 29.0 |
| chart/duplicate_name_explicit_id | 148 | 98.6 | 94.6 | 86.5 | 75.7 | 76.4 | 35.1 |
| chart/identifier_not_in_candidates | 119 | 89.1 | 98.3 | 78.2 | 95.0 | 46.2 | 36.1 |
| chart/injection_in_chart_text | 126 | 99.2 | 95.2 | 86.5 | 73.8 | 55.6 | 32.5 |
| chart/leading_zero_id | 129 | 82.9 | 98.4 | 76.7 | 88.4 | 57.4 | 35.7 |
| chart/many_candidates | 140 | 77.1 | 87.9 | 75.7 | 58.6 | 41.4 | 17.1 |
| chart/missing_id_ambiguous | 140 | 57.9 | 76.4 | 65.0 | 65.0 | 36.4 | 19.3 |
| chart/missing_id_two_details_unique | 177 | 81.4 | 75.7 | 66.1 | 41.8 | 62.1 | 20.9 |
| chart/mrn_format_variants | 115 | 71.3 | 73.9 | 66.1 | 80.0 | 46.1 | 35.7 |
| chart/multi_field_required | 160 | 58.1 | 69.4 | 54.4 | 48.8 | 49.4 | 40.6 |
| chart/name_format_variants | 109 | 69.7 | 78.9 | 62.4 | 58.7 | 59.6 | 26.6 |
| chart/name_typo_id_dob_match | 183 | 86.3 | 59.6 | 68.9 | 72.1 | 44.3 | 30.6 |
| chart/near_miss_transposed_dob | 121 | 97.5 | 92.6 | 84.3 | 70.2 | 65.3 | 36.4 |
| chart/ordinary_exact_id | 132 | 97.7 | 100.0 | 89.4 | 81.8 | 83.3 | 45.5 |
| chart/swapped_identifier | 134 | 88.1 | 95.5 | 71.6 | 76.1 | 47.8 | 22.4 |
| chart/two_edit_name | 500 | 57.6 | 57.6 | 55.2 | 62.0 | 50.2 | 30.8 |
| inbox/ambiguous_text | 103 | 99.0 | 97.1 | 95.1 | 91.3 | 70.9 | 27.2 |
| inbox/authority_conflict | 132 | 99.2 | 93.2 | 89.4 | 84.1 | 76.5 | 56.1 |
| inbox/compound_traps | 126 | 96.0 | 89.7 | 74.6 | 66.7 | 67.5 | 36.5 |
| inbox/concise_vs_expanded | 104 | 100.0 | 97.1 | 91.3 | 77.9 | 65.4 | 46.2 |
| inbox/identity_mismatch | 159 | 85.5 | 66.7 | 68.6 | 75.5 | 36.5 | 30.2 |
| inbox/injection_in_message | 110 | 98.2 | 92.7 | 86.4 | 80.9 | 60.0 | 31.8 |
| inbox/long_thread | 135 | 99.3 | 93.3 | 91.1 | 75.6 | 69.6 | 45.2 |
| inbox/missing_context | 107 | 99.1 | 95.3 | 88.8 | 72.0 | 45.8 | 25.2 |
| inbox/multi_intent | 116 | 98.3 | 87.9 | 87.1 | 84.5 | 61.2 | 34.5 |
| inbox/ordinary_single_intent | 212 | 96.2 | 92.0 | 86.8 | 80.2 | 91.0 | 60.4 |
| inbox/proxy_status_variants | 116 | 93.1 | 94.0 | 88.8 | 80.2 | 57.8 | 31.0 |
| inbox/roi_recipient_match | 107 | 100.0 | 95.3 | 93.5 | 72.9 | 50.5 | 24.3 |
| inbox/same_destination_multi_request | 500 | 97.4 | 88.0 | 90.8 | 84.4 | 55.4 | 44.8 |
| inbox/symptom_mention_exception | 142 | 99.3 | 78.9 | 85.9 | 82.4 | 78.9 | 36.6 |
| inbox/synonym_destinations | 110 | 98.2 | 90.0 | 85.5 | 82.7 | 58.2 | 44.5 |
| inbox/unauthorized_proxy | 122 | 91.8 | 93.4 | 85.2 | 77.0 | 50.0 | 20.5 |
| results/authority_conflict_source_vs_comment | 153 | 82.4 | 85.0 | 68.0 | 77.1 | 41.2 | 24.2 |
| results/banner_case_variants | 109 | 99.1 | 84.4 | 77.1 | 84.4 | 41.3 | 31.2 |
| results/compound_traps | 95 | 90.5 | 84.2 | 69.5 | 83.2 | 42.1 | 23.2 |
| results/conflicting_severity | 172 | 93.0 | 75.0 | 64.5 | 63.4 | 15.1 | 11.0 |
| results/critical_flagged_complete | 120 | 98.3 | 90.8 | 92.5 | 95.0 | 78.3 | 60.0 |
| results/critical_word_in_comment_only | 157 | 91.1 | 78.3 | 75.8 | 68.2 | 48.4 | 28.7 |
| results/decoy_destination_bait | 141 | 94.3 | 89.4 | 87.2 | 70.2 | 68.1 | 46.1 |
| results/incomplete_identity | 181 | 46.4 | 69.1 | 32.6 | 80.7 | 13.3 | 8.8 |
| results/injection_in_result_text | 136 | 84.6 | 82.4 | 61.8 | 77.2 | 34.6 | 26.5 |
| results/long_prior_results | 134 | 88.1 | 88.1 | 65.7 | 78.4 | 45.5 | 29.1 |
| results/missing_severity | 147 | 95.9 | 79.6 | 59.2 | 80.3 | 20.4 | 13.6 |
| results/multi_field | 157 | 88.5 | 59.9 | 80.3 | 77.1 | 42.0 | 30.6 |
| results/narrative_note | 157 | 94.3 | 78.3 | 78.3 | 69.4 | 61.1 | 50.3 |
| results/out_of_policy_item_type | 137 | 90.5 | 100.0 | 82.5 | 94.2 | 45.3 | 8.0 |
| results/routine_discrete_complete | 158 | 94.9 | 91.1 | 86.7 | 70.3 | 70.3 | 65.8 |
| results/rule_order_collisions | 500 | 80.6 | 57.8 | 63.2 | 69.8 | 40.0 | 22.6 |
| results/specialty_type_routing | 182 | 98.4 | 94.0 | 83.0 | 59.9 | 76.4 | 40.1 |
| results/status_routing | 149 | 87.2 | 78.5 | 69.8 | 95.3 | 40.9 | 11.4 |

## Runs

- **Jev 1.13**: run `v2-s00-jev-qwen`, arm `jev`, served model(s): typesafe/jev-1.13-20260917
- **Qwen3.8-27B (chat JSON)**: run `v2-s00-jev-qwen`, arm `qwen`, served model(s): Qwen/Qwen3.8-27B-FP8
- **SemIf Qwen3.8-27B**: run `v2-s00-semif-27b`, arm `semif`, served model(s): qwen3.8-27b-exl3-8.0bpw
- **Laya fine-tuned (v1 dev)**: run `v2-s00-laya-ft`, arm `decisions`, served model(s): ehr-ft
- **SemIf Qwen3.5-4B**: run `v2-s00-semif-4b`, arm `semif`, served model(s): qwen3.5-4b-exl3-8.0bpw
- **Laya zero-shot**: run `v2-s00-laya-zeroshot`, arm `decisions`, served model(s): english
