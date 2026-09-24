# Bank design (synthetic, administrative-only decision fixtures)

All names, MRNs, phones (555-01xx), emails (`@example.test`), addresses, message texts and result
notifications are **fabricated**. No real patient data, no diagnosis or treatment content: result text is
deliberately neutral and consistent with the source flag. Not affiliated with, endorsed by, or derived from
any EHR vendor's software.

Files:
- `generator/generate.py`: pure-stdlib Python 3.9+ generator. `python3 generator/generate.py --seed 20260923 --out data`
  writes `data/bank.jsonl`, `data/bank_summary.json` and `data/plans/plan_jev-qwen.json`. `--check` regenerates twice
  in temp dirs, asserts identical sha256, and compares with the files in `--out`. Historical seed cases are
  **not** distributed with this repo; they are filtered at write time by default (`--no-seeds` is the default).
- `generator/selfcheck.py`: schema and structural checks, plus an independent evidence-only re-derivation of
  labels (below). Exits non-zero on any error.
- `policy/policy.json` is the frozen policy the generator reads for catalogs; `policy/POLICY.md` is a readable
  copy; `policy/build_policy.py` regenerates both.
- `oracle/rules.py` is a label-blind rule baseline (see its README): it reads only `case_id`, `workflow`,
  `evidence` and `options` from the bank, never the labels.

## Label derivation
Each case begins as a set of structured ground-truth facts: the request and candidate identity fields; the
sender, proxy status, intents, symptom and ROI authorization; the result category, flags, status, cosign,
performing org and patient fields. Evaluators implement the policy rules in order on those facts
(`chart_eval`, `Inbox.eval`, `Results.eval`) and produce the label and `label_basis` (the rule id that fired).
Each scenario also states the label it intends, and the generator asserts that the evaluator agrees. Fillers
and distractors are added afterwards, and the chart label is recomputed and asserted unchanged.

Evidence is rendered from the facts. Style, synonyms and noise change only presentation that the policy tells
models to normalize: case, spacing, `Last, First` names, ISO/US/`Mon D, YYYY` dates, phone punctuation, and
typos in free text only. Protected tokens are never typo'd.

Independent verification: `selfcheck.py` re-parses the rendered chart and results evidence with a separate
implementation that never sees generator facts. Its labels agree with `expected` on 1789/1789 chart and
1783/1783 results non-ambiguous cases. Inbox bodies are free text, so only their structured preconditions are
cross-checked (1544/1544): proxy status, identity match, and the result-interface flag.

## Authority order (stated in every policy_text)
From highest to lowest: registration/account fields, then source-system structured fields, then free-text
claims (comments, chart notes, portal categories, suggested folders, thread history, prior results), then
embedded instructions, which are never followed and are never a reason to abstain on their own. The
`authority_conflict` and `injection` strata test this order, and it is also crossed into the other strata as a
label-neutral add-on.

## Strata (dev + holdout; siblings count toward their base's stratum)
Each stratum mixes definite labels with near-miss variants, so that neither the stratum name nor field
presence predicts the label. See `data/bank_summary.json` for exact per-stratum counts.

| workflow | stratum | notes |
|---|---|---|
| chart | ordinary_exact_id | MRN + name + DOB match |
| chart | duplicate_name_explicit_id | same name (±DOB), MRN disambiguates |
| chart | swapped_identifier | household member's MRN with other person's name/DOB → HR, or exact |
| chart | conflicting_dob_vs_id | MRN matches, DOB differs (±1d, ±1mo, ±1y, 10y) → HR; near-miss decoy DOB ±1d |
| chart | missing_id_two_details_unique | no MRN; DOB+contact or phone+email unique |
| chart | missing_id_ambiguous | same name+DOB twins / shared household phone / single detail → HR |
| chart | name_typo_id_dob_match | 1-edit typo + decoy registered under the typo'd name → select MRN |
| chart | near_miss_transposed_dob | decoy has month/day swapped |
| chart | identifier_not_in_candidates | MRN absent (1-digit-off or foreign) → HR |
| chart | leading_zero_id | `00xxxxxx` vs `xxxxxx` |
| chart | injection_in_chart_text | injection in request comment or chart note |
| chart | authority_conflict_comment_vs_registration | comments claim another chart / "DOB typo OK" |
| chart | multi_field_required | no MRN; multiple details must match |
| chart | no_safe_option | ambiguity set (descriptive) |
| inbox | ordinary_single_intent | cycles all destinations (patient, staff, pharmacy, insurer, outside-facility and result-interface senders) |
| inbox | multi_intent | two destinations → HR; two requests same destination → that destination; symptom + request → nurse_triage |
| inbox | ambiguous_text | no identifiable request → HR |
| inbox | missing_context | refill with no named medication; third-party ROI without authorization → HR |
| inbox | concise_vs_expanded | presentation-only siblings; same facts, same label |
| inbox | unauthorized_proxy | family sender with none/revoked/expired/other-person proxy → HR |
| inbox | symptom_mention_exception | symptom + request → nurse_triage; negated symptoms → route by request |
| inbox | injection_in_message | injection in the body or an imported comment; label by policy |
| inbox | synonym_destinations | synonym option descriptions + synonym phrasing |
| inbox | authority_conflict | staff folder suggestion / portal category / proxy claim vs account |
| inbox | identity_mismatch | stated name/DOB conflict with linked chart → HR |
| inbox | no_safe_option | descriptive |
| results | critical_flagged_complete / routine_discrete_complete / narrative_note / specialty_type_routing / status_routing / critical_word_in_comment_only / missing_severity / conflicting_severity / incomplete_identity / out_of_policy_item_type / injection_in_result_text / authority_conflict_source_vs_comment / multi_field / no_safe_option | see `bank_summary.json` and the policy rules |

Label mix (non-ambiguous): non-abstain share is 54.4% for chart, 59.5% for inbox, 54.9% for results
(`bank_summary.json.label_distribution`; chart candidate labels collapsed to `<candidate chart>`).

## Perturbation matrix
Each factor is assigned independently within each stratum using a balanced sequence (level counts differ by at
most 1, deterministically shuffled so factors pair randomly, Latin-square-style marginal balance):

- `n_options` 2/4/8/16: chart counts candidates plus abstain. For inbox/results, options are a catalog subset
  that always contains the correct key, contains abstain when available, and adds case-specific confusers first.
- `abstain_available`: true for every HR-labeled case and every counterfactual family; balanced about 50/50
  elsewhere. False plus an HR label occurs only in `no_safe_option`.
- `evidence_style` concise/expanded/noisy (typos in free text, uppercase or `Last, First` names, padded MRNs,
  alternate date/phone formats, junk fields).
- `distractors`: chart near-confuser candidates; inbox closed `thread_history`; results `prior_results`,
  sometimes flagged critical.
- `option_order`: canonical or shuffled. `synonyms`: reworded option descriptions / body phrasing / item types.
- `injection` and `authority_conflict`: always on in their dedicated strata and crossed into other strata at
  about 25%, label-neutral by construction.

## Counterfactual families
Fact-change bases per workflow (chart/inbox/results): 182/169/182, plus 60 inbox presentation pairs, with
359/294 (+60 presentation-only)/363 siblings. Each sibling has `counterfactual_of` and exactly one
`changed_fact`; its label comes from policy. Edits include DOB ±1 day, MRN added/removed/changed, names
replaced, proxy revoked or activated, symptom added or negated, second request added, med name removed,
source flag flipped, Final→Preliminary, cosign set, injections. Siblings share the base's options exactly and
its render seed; only the edited fact differs.

## Splits, repeat panel, ambiguity
- Split is about 80/20 dev/holdout, by family, stratified per workflow x stratum. Siblings never cross the split.
- `repeat_panel`: exactly 120 dev base cases (40 per workflow; 20 non-abstain and 20 HR each). All have a broad trial.
- `no_safe_option` (50 per workflow): the policy outcome is HR but abstain is not offered
  (`ambiguity.kind="no_valid_option"`, `expected="needs_human_review"`, deliberately not offered). Excluded
  from accuracy; kept to observe forced-choice behaviour.
- Irreducible ambiguity: none (0%).

## Trial plan (`data/plans/plan_jev-qwen.json`, 6226 trials <= 9400)
| phase | trials | contents |
|---|---|---|
| pilot | 90 | 30 per workflow; dev bases with no siblings, stratified |
| broad | 3262 | every other dev base case once |
| counterfactual | 852 | every dev sibling. Bases are not re-run: their comparison trial is their broad or pilot trial (see `plan.notes`) |
| repeat | 360 | 120 repeat-panel cases x reps 1..3 (rep 0 = broad) |
| load_c1/c2/c4/c8 | 4 x 150 | 50 per workflow sampled from dev (non-ambiguous); reps 10..13 |
| holdout | 1062 | every holdout case, dispatched only with `holdout` in `--phases` |

Trial order within each phase is a deterministic shuffle (`order_seed` 20260923); `arm_order` is drawn per
trial. Single-arm plan variants (`plan_jev.json`, `plan_qwen.json`, `plan_decisions.json`) carry the same
trial ids and order with a one-arm `arm_order`.

## Known limitations / judgment calls
- Policy choices that models may find debatable are stated explicitly in the policy text:
  a symptom report beats every other inbox rule (including an unauthorized proxy); chart contact-detail
  differences never block an exact MRN+DOB+name match; name tolerance is one edit in total (transpositions
  count as two, so none are generated); result status and cosign rules come after the critical rule.
- Inbox bodies are template-generated from a small number of templates per destination, so some destinations
  are thin and their per-destination accuracy is low-powered.
- Proxy messages use the "typing this for <patient>" convention, so the request text stays in the patient's voice.
- Chart `n_options=2` is underrepresented; for most chart strata n=2 with abstain would leave a single candidate.
- Siblings keep the base stratum name; filter on `counterfactual_of` to separate them. `field_condition` on a
  sibling describes the edit (e.g. `missing` when an MRN is removed).
