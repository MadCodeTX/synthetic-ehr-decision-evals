# Results — bank v1 (5,266 cases; 6,226 trials per model)

> Every model received the same 6,226 trials (same cases, options and option order). Accuracy = choice == frozen label, one scored trial per case (phases pilot/broad/counterfactual/holdout, rep 0); invalid outputs count as wrong; no_valid_option ambiguity cases are excluded.
> Jev, Qwen, SemIf and Laya ran on the pre-publication text of the bank (differs from data/bank.jsonl only in five result source-system names and one policy sentence); CLM ran on the published text. The 54 historical seed trials are omitted. Jev+Qwen paired run at concurrency 2; CLM at concurrency 6. The fine-tuned Laya and CLM heads were trained on this bank's dev split, so their v1 holdout numbers are in-distribution. See results/README.md.

## Headline

| metric | Clef-flash LoRA fine-tuned (v1 dev) | Clef 27B head fine-tuned (v1 dev, nf4) | Jev 1.13 | Clef-flash head fine-tuned (v1 dev) | Qwen3.8-27B (chat JSON) | Clef 27B zero-shot (nf4) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | CLM-8B fine-tuned (v1 dev) | Clef-flash zero-shot | SemIf Qwen3.5-4B | CLM-8B zero-shot | Laya zero-shot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Holdout accuracy % (95% CI) | 100.0 (99.6–100.0) | 96.2 (94.9–97.2) | 91.4 (89.5–92.9) | 89.8 (87.8–91.5) | 87.0 (84.8–88.9) | 79.6 (77.0–81.9) | 79.2 (76.6–81.5) | 79.1 (76.5–81.4) | 64.2 (61.3–67.1) | 53.0 (50.0–56.0) | 50.4 (47.3–53.4) | 41.5 (38.5–44.5) | 29.2 (26.5–32.0) |
| Holdout n | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 | 1032 |
| Dev accuracy % | 99.9 | 98.5 | 90.8 | 95.3 | 86.1 | 80.2 | 78.5 | 87.5 | 82.6 | 54.6 | 52.4 | 43.2 | 30.8 |
| Holdout chart / inbox / results % | 100.0 / 100.0 / 100.0 | 92.3 / 99.7 / 97.3 | 88.2 / 97.4 / 89.6 | 89.6 / 90.1 / 89.9 | 87.4 / 91.1 / 83.3 | 80.2 / 84.8 / 74.6 | 77.7 / 88.7 / 72.7 | 72.8 / 89.4 / 76.8 | 50.3 / 76.8 / 67.8 | 55.2 / 54.0 / 50.0 | 47.0 / 63.6 / 42.9 | 53.0 / 33.1 / 36.9 | 29.4 / 34.4 / 24.6 |
| Abstain when expected % | 99.9 | 98.7 | 82.1 | 91.7 | 85.6 | 60.4 | 70.5 | 87.8 | 91.9 | 15.0 | 17.1 | 73.0 | 10.2 |
| False abstain % (answer existed) | 0.1 | 2.4 | 1.5 | 3.3 | 10.0 | 3.7 | 13.7 | 14.3 | 18.3 | 3.0 | 4.2 | 44.7 | 7.7 |
| Counterfactual siblings acc % | 99.9 | 97.2 | 87.2 | 93.7 | 82.2 | 74.2 | 71.8 | 82.6 | 83.0 | 38.5 | 41.0 | 52.7 | 20.2 |
| Valid output % | 100.0 | 100.0 | 100.0 | 100.0 | 96.8 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| Latency p50 / p95 ms | 710 / 1100 | 2724 / 4065 | 312 / 1107 | 768 / 1188 | 1759 / 3096 | 2948 / 4417 | 3535 / 4867 | 80 / 102 | 373 / 886 | 731 / 1126 | 717 / 996 | 366 / 874 | 79 / 102 |
| p50 ms at concurrency 1 → 8 | 119 → 975 | 457 → 3836 | 233 → 286 | 125 → 1085 | 484 → 2418 | 499 → 4112 | 659 → 4721 | 70 → 90 | 32 → 32 | 120 → 1021 | 174 → 986 | 32 → 33 | 70 → 92 |
| Repeat panel identical across 4 reps % | 100.0 | 100.0 | 96.7 | 100.0 | 99.2 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| Calibration ECE (chosen prob.) | 0.0008 | 0.0079 | 0.016 | 0.014 | — | 0.0739 | 0.0286 | 0.0386 | 0.0183 | 0.3003 | 0.2578 | 0.3319 | 0.2393 |
| Reported API cost, whole run (USD) | $0.00 | $0.00 | $0.35 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 | $0.00 |
| Trials | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 | 6226 |

## Accuracy by stratum (dev + holdout, one scored trial per case)

| workflow / stratum | n | Clef-flash LoRA fine-tuned (v1 dev) | Clef 27B head fine-tuned (v1 dev, nf4) | Jev 1.13 | Clef-flash head fine-tuned (v1 dev) | Qwen3.8-27B (chat JSON) | Clef 27B zero-shot (nf4) | SemIf Qwen3.8-27B | Laya fine-tuned (v1 dev) | CLM-8B fine-tuned (v1 dev) | Clef-flash zero-shot | SemIf Qwen3.5-4B | CLM-8B zero-shot | Laya zero-shot |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| chart/authority_conflict_comment_vs_registration | 139 | 100.0 | 95.7 | 79.9 | 92.1 | 89.2 | 79.9 | 73.4 | 82.0 | 67.6 | 34.5 | 27.3 | 57.6 | 25.9 |
| chart/conflicting_dob_vs_id | 139 | 99.3 | 97.1 | 95.7 | 92.1 | 89.2 | 68.3 | 89.9 | 76.3 | 71.2 | 44.6 | 54.7 | 69.1 | 20.1 |
| chart/duplicate_name_explicit_id | 135 | 100.0 | 96.3 | 99.3 | 98.5 | 94.8 | 97.8 | 94.8 | 84.4 | 42.2 | 83.0 | 76.3 | 20.7 | 39.3 |
| chart/identifier_not_in_candidates | 140 | 100.0 | 99.3 | 88.6 | 97.1 | 95.7 | 89.3 | 77.9 | 95.0 | 75.7 | 41.4 | 37.9 | 72.9 | 29.3 |
| chart/injection_in_chart_text | 134 | 100.0 | 97.8 | 99.3 | 98.5 | 94.8 | 94.0 | 87.3 | 91.8 | 59.7 | 48.5 | 49.3 | 57.5 | 29.1 |
| chart/leading_zero_id | 136 | 100.0 | 98.5 | 88.2 | 97.1 | 98.5 | 83.1 | 80.9 | 93.4 | 77.9 | 58.1 | 45.6 | 50.7 | 18.4 |
| chart/missing_id_ambiguous | 135 | 100.0 | 93.3 | 75.6 | 97.0 | 83.0 | 77.8 | 74.1 | 77.8 | 73.3 | 51.9 | 34.1 | 66.7 | 27.4 |
| chart/missing_id_two_details_unique | 131 | 100.0 | 91.6 | 91.6 | 95.4 | 78.6 | 90.1 | 71.8 | 58.0 | 44.3 | 76.3 | 57.3 | 24.4 | 20.6 |
| chart/multi_field_required | 137 | 100.0 | 94.9 | 65.7 | 92.7 | 68.6 | 70.8 | 66.4 | 69.3 | 56.2 | 56.2 | 38.0 | 39.4 | 21.9 |
| chart/name_typo_id_dob_match | 141 | 100.0 | 91.5 | 83.7 | 86.5 | 63.1 | 70.2 | 58.9 | 84.4 | 60.3 | 55.3 | 39.0 | 51.1 | 32.6 |
| chart/near_miss_transposed_dob | 138 | 100.0 | 100.0 | 94.9 | 94.2 | 93.5 | 78.3 | 87.7 | 84.8 | 59.4 | 55.1 | 57.2 | 51.4 | 26.8 |
| chart/ordinary_exact_id | 144 | 100.0 | 97.2 | 97.9 | 98.6 | 95.8 | 93.1 | 86.8 | 84.0 | 43.8 | 84.7 | 84.7 | 21.5 | 39.6 |
| chart/swapped_identifier | 140 | 100.0 | 96.4 | 94.3 | 92.9 | 95.7 | 76.4 | 80.7 | 83.6 | 74.3 | 49.3 | 49.3 | 70.7 | 32.9 |
| inbox/ambiguous_text | 118 | 100.0 | 100.0 | 100.0 | 96.6 | 96.6 | 96.6 | 95.8 | 100.0 | 97.5 | 57.6 | 68.6 | 47.5 | 28.8 |
| inbox/authority_conflict | 143 | 100.0 | 99.3 | 97.9 | 95.1 | 87.4 | 88.8 | 86.0 | 94.4 | 80.4 | 74.1 | 70.6 | 21.7 | 46.2 |
| inbox/concise_vs_expanded | 120 | 100.0 | 100.0 | 97.5 | 97.5 | 96.7 | 85.0 | 91.7 | 96.7 | 94.2 | 51.7 | 55.0 | 33.3 | 30.0 |
| inbox/identity_mismatch | 138 | 99.3 | 94.2 | 84.8 | 82.6 | 75.4 | 41.3 | 67.4 | 85.5 | 85.5 | 28.3 | 33.3 | 44.2 | 24.6 |
| inbox/injection_in_message | 131 | 100.0 | 100.0 | 99.2 | 98.5 | 90.1 | 93.9 | 87.0 | 99.2 | 88.5 | 55.7 | 61.8 | 29.8 | 35.9 |
| inbox/missing_context | 147 | 100.0 | 100.0 | 99.3 | 93.9 | 95.9 | 88.4 | 95.9 | 93.9 | 95.9 | 33.3 | 40.1 | 48.3 | 24.5 |
| inbox/multi_intent | 135 | 97.8 | 99.3 | 97.8 | 96.3 | 88.1 | 90.4 | 89.6 | 92.6 | 93.3 | 45.2 | 56.3 | 37.0 | 26.7 |
| inbox/ordinary_single_intent | 198 | 100.0 | 100.0 | 99.0 | 97.5 | 96.0 | 98.5 | 92.4 | 97.5 | 89.4 | 83.8 | 91.9 | 25.8 | 60.6 |
| inbox/symptom_mention_exception | 143 | 100.0 | 100.0 | 99.3 | 100.0 | 84.6 | 90.2 | 88.8 | 97.2 | 92.3 | 55.2 | 82.5 | 37.8 | 27.3 |
| inbox/synonym_destinations | 131 | 100.0 | 100.0 | 98.5 | 98.5 | 90.8 | 90.8 | 90.8 | 96.2 | 93.1 | 60.3 | 64.9 | 44.3 | 37.4 |
| inbox/unauthorized_proxy | 140 | 100.0 | 99.3 | 93.6 | 95.7 | 89.3 | 71.4 | 87.1 | 84.3 | 80.7 | 54.3 | 52.1 | 37.9 | 28.6 |
| results/authority_conflict_source_vs_comment | 138 | 100.0 | 99.3 | 82.6 | 92.8 | 75.4 | 84.8 | 65.9 | 82.6 | 87.0 | 58.0 | 44.2 | 35.5 | 25.4 |
| results/conflicting_severity | 141 | 100.0 | 95.0 | 94.3 | 85.8 | 80.1 | 52.5 | 56.7 | 69.5 | 84.4 | 19.1 | 14.9 | 49.6 | 11.3 |
| results/critical_flagged_complete | 140 | 100.0 | 96.4 | 96.4 | 97.1 | 88.6 | 95.7 | 92.9 | 94.3 | 91.4 | 92.9 | 74.3 | 30.7 | 62.1 |
| results/critical_word_in_comment_only | 134 | 100.0 | 99.3 | 96.3 | 88.8 | 84.3 | 75.4 | 71.6 | 75.4 | 83.6 | 56.0 | 50.7 | 36.6 | 33.6 |
| results/incomplete_identity | 139 | 100.0 | 100.0 | 42.4 | 89.9 | 64.0 | 30.2 | 26.6 | 87.8 | 90.6 | 14.4 | 9.4 | 60.4 | 6.5 |
| results/injection_in_result_text | 139 | 100.0 | 99.3 | 84.9 | 93.5 | 78.4 | 69.1 | 61.9 | 84.9 | 90.6 | 48.2 | 42.4 | 42.4 | 30.9 |
| results/missing_severity | 142 | 100.0 | 99.3 | 91.5 | 97.9 | 77.5 | 62.0 | 56.3 | 84.5 | 94.4 | 12.7 | 12.0 | 56.3 | 6.3 |
| results/multi_field | 137 | 100.0 | 100.0 | 88.3 | 91.2 | 71.5 | 72.3 | 74.5 | 77.4 | 83.9 | 56.9 | 43.8 | 35.0 | 28.5 |
| results/narrative_note | 124 | 100.0 | 99.2 | 96.8 | 92.7 | 77.4 | 89.5 | 84.7 | 77.4 | 85.5 | 66.1 | 60.5 | 20.2 | 56.5 |
| results/out_of_policy_item_type | 136 | 100.0 | 100.0 | 85.3 | 88.2 | 99.3 | 64.0 | 72.1 | 97.1 | 96.3 | 34.6 | 40.4 | 58.8 | 4.4 |
| results/routine_discrete_complete | 140 | 100.0 | 99.3 | 97.1 | 91.4 | 95.7 | 94.3 | 87.9 | 77.1 | 85.7 | 74.3 | 77.9 | 37.9 | 65.7 |
| results/specialty_type_routing | 137 | 100.0 | 100.0 | 96.4 | 96.4 | 90.5 | 91.2 | 81.0 | 69.3 | 78.8 | 79.6 | 71.5 | 28.5 | 30.7 |
| results/status_routing | 136 | 100.0 | 99.3 | 89.0 | 93.4 | 76.5 | 75.0 | 72.8 | 91.9 | 67.6 | 48.5 | 39.7 | 35.3 | 19.1 |

## Runs

- **Clef-flash LoRA fine-tuned (v1 dev)**: run `v1-clef-flash-ft-lora`, arm `decisions`, served model(s): clef-flash-ft-lora
- **Clef 27B head fine-tuned (v1 dev, nf4)**: run `v1-clef-27b-nf4-ft-head`, arm `decisions`, served model(s): clef-nf4-ft-head
- **Jev 1.13**: run `2026-09-23T01-47-00Z-qwen-jev`, arm `jev`, served model(s): typesafe/jev-1.13-20260917
- **Clef-flash head fine-tuned (v1 dev)**: run `v1-clef-flash-ft-head`, arm `decisions`, served model(s): clef-flash-ft-head
- **Qwen3.8-27B (chat JSON)**: run `2026-09-23T01-47-00Z-qwen-jev`, arm `qwen`, served model(s): Qwen/Qwen3.8-27B-FP8
- **Clef 27B zero-shot (nf4)**: run `v1-clef-27b-nf4-zeroshot`, arm `decisions`, served model(s): clef-nf4
- **SemIf Qwen3.8-27B**: run `2026-09-23T16-04-28Z-semif-27b`, arm `semif`, served model(s): qwen3.8-27b-exl3-8.0bpw
- **Laya fine-tuned (v1 dev)**: run `2026-09-23T14-44-51Z-laya-ft`, arm `laya`, served model(s): ehr-ft
- **CLM-8B fine-tuned (v1 dev)**: run `v1-clm-ft`, arm `decisions`, served model(s): clm-ft-v1dev
- **Clef-flash zero-shot**: run `v1-clef-flash-zeroshot`, arm `decisions`, served model(s): clef-flash
- **SemIf Qwen3.5-4B**: run `2026-09-23T15-43-56Z-semif-4b`, arm `semif`, served model(s): qwen3.5-4b-exl3-8.0bpw
- **CLM-8B zero-shot**: run `v1-clm-zeroshot`, arm `decisions`, served model(s): clm-latest
- **Laya zero-shot**: run `2026-09-23T13-41-27Z-laya-gpu`, arm `laya`, served model(s): english
