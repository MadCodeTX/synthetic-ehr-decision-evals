# Rule baseline (label-blind)

A deterministic implementation of the frozen routing policy
(`policy/policy.json` v1, `policy/POLICY.md`), written without looking at the generator's labels.
Python 3.9, stdlib only.

```
python3 oracle/rules.py --bank data/bank.jsonl --policy policy/policy.json --out /tmp/rule_predictions.jsonl
# also writes rule_summary.json next to --out (override with --summary PATH)
# bank v2: add --extra-vocab data/v2/vocab.txt (known words the typo normalizer must not rewrite)
```

Output: one line per case, `{case_id, rule_choice, rule_reason}`. `rule_choice` is an option key from
that case, or `null`. `rule_reason` names the policy rule that fired. Two runs give byte-identical output.

## How it stays blind to the labels
- `load_bank()` keeps only `case_id`, `workflow`, `evidence` and `options`, and drops every other field
  as soon as each line is parsed. The code never reads `expected`, `label_basis`, `stratum`,
  `perturbations`, `changed_fact`, `ambiguity` or `family_id`, and never prints them.
- Built only from POLICY.md/policy.json, SPEC §2 and the allowed fields of the bank. `generate.py`,
  `selfcheck.py` and `fixtures/README.md` were not opened.
- `rule_summary.json` only counts outcomes. No accuracy is computed.

## Choosing the output key
1. The policy picks a destination.
2. If that destination is an offered option key, it is emitted. Legacy seed results options use display
   titles such as `"Critical Results"`; these are matched through the destination description title.
3. If the policy destination is not offered, the rule emits the abstain key (`needs_human_review`, or
   `abstain` for seed cases) when that key is offered.
4. Otherwise the output is `null`. This covers two cases: the policy says human review but the abstain
   option is not offered, or the rule cannot apply to the format (the 18 legacy seed inbox narratives).

## chart
- Request field synonyms: `mrn|medical_record_number|patient_id`, `patient_name|name_on_request|name`,
  `dob|date_of_birth`, `phone|callback_number`, `email|email_address`, `address|home_address`.
- Candidate field synonyms: `chart_id|id`, `name|registered_name`, `dob|date_of_birth`,
  `phone|phone_on_file`, `email|email_on_file`, `address|address_on_file`.
- Normalization:
  - MRN must be an exact string match after trimming surrounding spaces. Leading zeros count.
  - DOB is compared as a calendar date, parsed from ISO, US `MM/DD/YYYY` or `Mon D, YYYY`.
  - Phone compares digits only.
  - Email and address keep only lowercase letters and digits.
  - Names: take the first and last token after removing punctuation and middle names or initials, and
    handling `Last, First` order. They match when the total Levenshtein distance is 1 or less.
- Rule 1 (MRN given):
  - No candidate with that MRN → review.
  - DOB conflict, or DOB missing on the candidate → review.
  - Name mismatch → review.
  - Otherwise select that candidate. Contact-detail differences are ignored.
- Rule 2 (no MRN): a candidate qualifies when the name matches, the request supplies at least 2 of
  DOB/phone/email/address, and every supplied field equals the candidate's value. A candidate missing a
  supplied field does not qualify. Exactly one qualifying candidate → select it; otherwise review.
- Rule 3: `comment`, `chart_note`, `fax_header`, `source`, `message`, `pcp`, `status` and `last_visit`
  are never read.
- Seed chart cases (legacy format): the narrow registration phrases in `detail` are parsed:
  `Phone ending NNNN` (compared on the last 4 digits), `Email name@example.test`, `Address listed as ...` and
  `address on file: ...`. The rest of `detail` is ignored.

## inbox (ordered, first match wins)
Only `item.body` (the newest message) is read. Before any matching, the text goes through three steps:
1. Lowercase and collapse whitespace.
2. Typo normalization: any word of 5 or more letters that is not in the keyword vocabulary is replaced
   by the single vocabulary word within one edit or adjacent swap (e.g. `rfeill` → `refill`).
3. Embedded instructions (`INJECTION_PATTERNS`) are removed and never followed. They are also never a
   reason to abstain; the reason string just notes `[embedded instruction ignored]`.

`subject`, `portal_category`, `comments`, `thread_history` and folder hints are never used.

1. **Symptom.** Negated statements are removed first (`NEGATION_PATTERNS`). Any match in
   `SYMPTOM_PATTERNS` → `nurse_triage`. This overrides every later rule.
2. **Identity.** If `regarding_patient` is present and its name differs from `linked_chart` by more than
   one letter, or its DOB is a different date → review.
3. **Proxy.** Sender roles outside {patient, clinic staff, pharmacy, insurer, outside facility, result
   interface} need an `account_flags.proxy_access` entry with status `active` and the sender's name
   (exact normalized match). Otherwise → review. Claims in the message body such as "I am listed as an
   authorized proxy" are ignored.
   - Result-interface senders are routed by `result_info`: `source_severity_flag` critical →
     `critical_results`; discrete → `results`; narrative → `result_notes`.
4. **Intent detection.** The body is split into sentences. Each sentence gets the first matching
   destination in `INTENT_PATTERNS` (keyword regexes, visible in code). `FILLER_PATTERNS` covers
   pleasantries, request details and vague text.
   - If no body intent is found, a clinic-staff sender maps to `staff_messages` and an outside-facility
     sender maps to `outside_records`.
   - Still no intent → review (rule 4).
5. **Multiple intents.** Two or more different destinations → review. Several requests for the same
   destination route there.
6. **Incomplete request.**
   - Refill without a named medication (no dose pattern and no name from `MED_NAMES`) → review.
   - Records release to a third party (`send my records ... to X` or `fax my visit notes to X`) without an
     active `roi_authorizations` entry whose recipient equals X → review.
   - Records sent to the patient (portal, printout, own files) need no authorization.
7. **Destination.** Route to the single detected destination.

## results (ordered, structured fields only)
1. `item_type` must classify as a result type (`RESULT_TYPE_KEYWORDS`) and `message_class` must be
   `Result`. Otherwise → review.
2. Patient name, MRN or DOB missing → review.
3. `source_flag` missing → review. `notification_banner` present and different from `source_flag`
   (case-insensitive) → review.
4. `source_flag` critical → `critical_results`.
5. `result_status` missing → review. Then, in order: `requires_cosign` → `cosign_pending`;
   Corrected/Amended → `corrected_results`; Preliminary → `preliminary_results`.
6. `performing_org_type` outside → `outside_results`.
7. Route by type: `imaging_results`, `pathology_review`, `microbiology_review`, `cardiology_results`,
   `result_notes` or `results`.

`comment`, `result_text` and `prior_results` are never read. Decoy destinations such as genetics,
research, screening, send-out, orders and specimen issues are never produced, because the policy never
routes to them.

Legacy seed results use a different format and are handled this way:
- `item_type` must be a discrete or narrative result.
- `routing_completeness` must be `complete` (standing in for the identity and status fields).
- `severity_flag` and `source_severity` must both be present and equal. Critical →
  `Critical Results`; otherwise the route follows the type.
- The free-text `evidence` field is ignored. For example, seed-res-014, whose free text says the item is
  not a result, still routes as a result.

## Known limitations
- Inbox intent detection is keyword-based and tuned to the phrasings seen in the bank's allowed fields.
  A new phrasing may land in rule 4 (review) or be missed.
- The reason string flags `[N unparsed sentence(s)]` when a routed message also contained sentences
  the rule could not classify.
- The 18 legacy seed inbox cases are narrative summaries without sender, linked_chart or account
  fields, so rules 2 and 3 cannot be evaluated. They are emitted as `null`.
