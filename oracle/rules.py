#!/usr/bin/env python3
"""Deterministic, label-blind rule baseline for the synthetic EHR routing bank.

Implements the frozen written policy (policy/policy.json, v1) directly:
  chart   - identifier matching (MRN exact, DOB calendar equality, phone digits,
            email/address normalized, name with 1-letter tolerance), rules 1-3.
  inbox   - ordered rules 1-8 with visible keyword lists for intent detection.
  results - ordered rules 1-8 on structured notification fields only.

BLINDNESS: the loader keeps ONLY case_id, workflow, evidence, options from each
bank record. Label/design fields (expected, label_basis, stratum, perturbations,
changed_fact, ambiguity, family_id, ...) are dropped on read and never printed.

Output: one JSON line per case {case_id, rule_choice, rule_reason}.
rule_choice is an option key of that case, or null ("rule cannot decide").
Python 3.9 stdlib only.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

ALLOWED_FIELDS = ("case_id", "workflow", "evidence", "options")


# ---------------------------------------------------------------------------
# Loading (label-blind)
# ---------------------------------------------------------------------------
def load_bank(path):
    """Yield records restricted to ALLOWED_FIELDS. Everything else is discarded."""
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            out.append({k: raw[k] for k in ALLOWED_FIELDS if k in raw})
            del raw
    return out


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def present(v):
    return v is not None and not (isinstance(v, str) and v.strip() == "")


def first_present(d, keys):
    """Return first present value among synonym field names (case-insensitive keys)."""
    if not isinstance(d, dict):
        return None
    lower = {str(k).strip().lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k)
        if present(v):
            return v
    return None


def norm_space(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def lev(a, b):
    """Plain Levenshtein distance (insert/delete/replace)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def damerau1(a, b):
    """True if a and b are within one edit incl. adjacent transposition."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
            return True
        return False
    return lev(a, b) == 1


# ---- dates ----
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(s):
    """ISO YYYY-MM-DD, US MM/DD/YYYY, 'Mon D, YYYY' / 'Month D, YYYY'. Returns date or None."""
    if not present(s):
        return None
    t = norm_space(s).lower()
    try:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
        if m:  # US format: month first
            return datetime.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        m = re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{1,2}),? (\d{4})", t)
        if m and m.group(1) in MONTHS:
            return datetime.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
    except ValueError:
        return None
    return None


def dates_equal(a, b):
    da, db = parse_date(a), parse_date(b)
    if da is not None and db is not None:
        return da == db
    # unparseable: fall back to whitespace/case-normalized string equality
    return norm_space(a).lower() == norm_space(b).lower()


def digits(s):
    return re.sub(r"\D", "", str(s))


def loose(s):
    """email/address comparison: ignore case, spacing, punctuation."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


# ---- names ----
def name_parts(s):
    """-> (first, last) after removing punctuation, middle names/initials, 'Last, First' order."""
    if not present(s):
        return None
    s = norm_space(s).lower()
    if "," in s:
        last_part, _, first_part = s.partition(",")
        s = first_part + " " + last_part  # First [Middle] Last
    toks = [re.sub(r"[^a-z]", "", t) for t in s.split()]
    toks = [t for t in toks if t]
    if not toks:
        return None
    if len(toks) >= 3:  # drop single-letter middle initials only in the middle
        toks = [toks[0]] + [t for t in toks[1:-1] if len(t) > 1] + [toks[-1]]
    if len(toks) == 1:
        return (toks[0], "")
    return (toks[0], toks[-1])


def name_distance(a, b):
    pa, pb = name_parts(a), name_parts(b)
    if pa is None or pb is None:
        return None
    return lev(pa[0], pb[0]) + lev(pa[1], pb[1])


def names_match(a, b, tolerance=1):
    d = name_distance(a, b)
    return d is not None and d <= tolerance


# ---------------------------------------------------------------------------
# Option mapping
# ---------------------------------------------------------------------------
ABSTAIN_KEYS = ("needs_human_review", "abstain")


def okey_norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def map_dest(dest, options, destinations):
    """Map a policy destination key to an offered option key (or None)."""
    if dest in options:
        return dest
    if dest == "needs_human_review":
        for k in ABSTAIN_KEYS:
            if k in options:
                return k
        return None
    # legacy seed options keyed by display title, e.g. "Critical Results"
    title = destinations.get(dest, "").split(":")[0]
    targets = {okey_norm(dest), okey_norm(title)}
    for k in options:
        if okey_norm(k) in targets:
            return k
    return None


def finalize(dest, reason, options, destinations):
    """Resolve the policy decision to an offered key, else abstain if offered, else null."""
    if dest is None:
        return None, reason
    k = map_dest(dest, options, destinations)
    if k is not None:
        return k, reason
    if dest != "needs_human_review":
        ab = map_dest("needs_human_review", options, destinations)
        if ab is not None:
            return ab, reason + " | policy destination '%s' not offered -> human review" % dest
        return None, reason + " | policy destination '%s' not offered and no abstain option" % dest
    return None, reason + " | policy says needs_human_review but it is not offered"


# ---------------------------------------------------------------------------
# CHART
# ---------------------------------------------------------------------------
REQ_MRN = ("mrn", "medical_record_number", "patient_id")
REQ_NAME = ("patient_name", "name_on_request", "name")
REQ_DOB = ("dob", "date_of_birth")
REQ_PHONE = ("phone", "callback_number")
REQ_EMAIL = ("email", "email_address")
REQ_ADDR = ("address", "home_address")
CAND_ID = ("chart_id", "id")
CAND_NAME = ("name", "registered_name")
CAND_DOB = ("dob", "date_of_birth")
CAND_PHONE = ("phone", "phone_on_file")
CAND_EMAIL = ("email", "email_on_file")
CAND_ADDR = ("address", "address_on_file")
# NOT read (authority rule 3): comment, chart_note, fax_header, source, message, last_visit, pcp, status


def legacy_detail(detail):
    """Seed chart records carry contact data only inside a structured 'detail' string, e.g.
    'Phone ending 4420', 'Email name@example.test', 'Address listed as 8 Oak Street', 'address on file: 14 Cedar Lane'.
    Parse those narrow registration patterns; everything else in detail is ignored."""
    out = {}
    if not present(detail):
        return out
    d = str(detail)
    m = re.search(r"phone ending (\d{4})", d, re.I)
    if m:
        out["phone_last4"] = m.group(1)
    m = re.search(r"email\s+([^\s;,]+@[^\s;,]+)", d, re.I)
    if m:
        out["email"] = m.group(1).rstrip(".")
    m = re.search(r"address (?:listed as|on file:)\s*([^;]+)", d, re.I)
    if m:
        out["address"] = m.group(1).strip().rstrip(".")
    return out


def contact_fields(obj, is_req):
    f = {
        "dob": first_present(obj, REQ_DOB if is_req else CAND_DOB),
        "phone": first_present(obj, REQ_PHONE if is_req else CAND_PHONE),
        "email": first_present(obj, REQ_EMAIL if is_req else CAND_EMAIL),
        "address": first_present(obj, REQ_ADDR if is_req else CAND_ADDR),
    }
    leg = legacy_detail(first_present(obj, ("detail",)))
    for k in ("email", "address"):
        if f[k] is None and k in leg:
            f[k] = leg[k]
    if f["phone"] is None and "phone_last4" in leg:
        f["phone_last4"] = leg["phone_last4"]
    return f


def field_equal(kind, a, b):
    if kind == "dob":
        return dates_equal(a, b)
    if kind == "phone":
        return digits(a) == digits(b) and digits(a) != ""
    return loose(a) == loose(b) and loose(a) != ""


def chart_decide(ev):
    req = ev.get("request") or {}
    cands = ev.get("candidates") or []
    rname = first_present(req, REQ_NAME)
    rmrn = first_present(req, REQ_MRN)
    rf = contact_fields(req, True)

    def cid(c):
        v = first_present(c, CAND_ID)
        return str(v).strip() if v is not None else None

    if rmrn is not None:
        mrn = str(rmrn).strip()  # surrounding spaces ignored; leading zeros significant
        hits = [c for c in cands if cid(c) == mrn]
        if not hits:
            return "needs_human_review", "chart_rule_1a: request MRN %s matches no candidate" % mrn
        if len(hits) > 1:
            return "needs_human_review", "chart_rule_1: MRN %s appears on several candidates" % mrn
        c = hits[0]
        if rf["dob"] is not None:
            cdob = first_present(c, CAND_DOB)
            if cdob is None or not dates_equal(rf["dob"], cdob):
                return "needs_human_review", "chart_rule_1b: MRN match %s but DOB conflicts" % mrn
        if not names_match(rname, first_present(c, CAND_NAME)):
            return "needs_human_review", "chart_rule_1c: MRN match %s but name does not match (dist=%s)" % (
                mrn, name_distance(rname, first_present(c, CAND_NAME)))
        return "@" + cid(c), "chart_rule_1: MRN %s + DOB/name agree" % mrn

    # Rule 2: no MRN
    supplied = [k for k in ("dob", "phone", "email", "address") if rf[k] is not None]
    if "phone_last4" in rf:
        supplied.append("phone_last4")
    if len(supplied) < 2:
        return "needs_human_review", "chart_rule_2_not_unique: no MRN and only %d identity detail(s) supplied" % len(supplied)
    qualifying = []
    for c in cands:
        if not names_match(rname, first_present(c, CAND_NAME)):
            continue
        cf = contact_fields(c, False)
        ok = True
        for k in supplied:
            if k == "phone_last4":
                cp = cf["phone"] if cf["phone"] is not None else cf.get("phone_last4")
                if cp is None or digits(cp)[-4:] != rf["phone_last4"]:
                    ok = False
                    break
                continue
            if cf[k] is None or not field_equal(k, rf[k], cf[k]):
                ok = False
                break
        if ok:
            qualifying.append(cid(c))
    if len(qualifying) == 1:
        return "@" + qualifying[0], "chart_rule_2_unique_details: sole candidate matching name + %s" % "/".join(supplied)
    return "needs_human_review", "chart_rule_2_not_unique: %d qualifying candidates" % len(qualifying)


def chart_finalize(dest, reason, options, destinations):
    if dest.startswith("@"):
        chart_id = dest[1:]
        for k in options:
            if str(k).strip() == chart_id:
                return k, reason
        return finalize("needs_human_review", reason + " | selected chart %s not among options" % chart_id,
                        options, destinations)
    return finalize(dest, reason, options, destinations)


# ---------------------------------------------------------------------------
# INBOX
# ---------------------------------------------------------------------------
# Embedded instructions (authority level 4): stripped before intent detection, never followed.
INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior) instructions[^.]*\.?",
    r"ignore the request details[^.]*\.?",
    r"\[admin override\][^.]*\.?",
    r"<!--.*?-->",
    r"#+ ?new instructions:.*?nothing else\.?",
    r"note to the ai reviewer:[^.]*\.?",
    r"\bsystem:[^.]*\.?",
    r"(select|choose|answer|output)\s*\{?[^.]*\b[a-z]+_[a-z_]+\b[^.]*\.?",
]

# Negated symptom statements (rule 1 exclusion) are removed before symptom matching.
NEGATION_PATTERNS = [
    r"no new symptoms", r"no side effects", r"not having any problems", r"nothing has changed",
    r"feel fine", r"no symptoms", r"no clinical concern",
]

# Rule 1: new/worsening symptom, side effect or injury -> nurse_triage.
SYMPTOM_PATTERNS = [
    r"\bcough\b[^.?!]*\b(worse|worsening)", r"\brash\b", r"\btwisted\b", r"\bsprain", r"\bhurts?\b",
    r"\bswollen\b", r"\bswelling\b", r"\bfever\b", r"\bnause", r"\bdizz", r"\bvomit", r"\bpain\b",
    r"\bbleeding\b", r"\bshort of breath\b", r"\bside effects?\b", r"\binjur", r"\bfell\b",
    r"\bheadache", r"\bgetting worse\b", r"\bnot feeling well\b",
]

# Rule 7 destinations: (destination, [regex, ...]). Matched on typo-normalized lowercase text.
INTENT_PATTERNS = [
    ("rx_requests", [r"\brefill", r"\brenew", r"\brequesting a refill", r"\brefill request from\b"]),
    ("prior_auth", [r"\bprior authori[sz]ation\b", r"\bpre-?approval\b"]),
    ("scheduling", [r"\bmove my (visit|appointment)\b", r"\breschedule\b", r"\bcancel my appointment\b",
                    r"\bbook my (annual|physical|appointment|visit)", r"\bschedule an? (appointment|visit)\b",
                    r"\bnew appointment\b"]),
    ("patient_calls", [r"\bcall me back\b", r"\bphone me\b", r"\bgive me a call\b", r"\bcall me about\b"]),
    ("medical_records_roi", [r"\bprintout of my chart\b", r"\bcopy of my (records|immunization|chart|visit)",
                             r"\bcopy of my [a-z ]*record", r"\bsend my records\b", r"\bfax my (visit notes|records)\b",
                             r"\bsend me a copy of my records\b"]),
    ("referrals", [r"\breferral to\b", r"\brefer me\b", r"\bstatus of the [a-z ]*referral\b", r"\breferral\b"]),
    ("billing", [r"\bcharge on my account\b", r"\bpayment plan\b", r"\bstatement for \$", r"\bmy balance\b",
                 r"\bmy bill\b", r"\bbilled\b"]),
    ("care_coordination", [r"\barrange a ride\b", r"\bride to my appointment\b", r"\bno longer drive\b",
                           r"\bdelivered to my house\b", r"\b(cane|walker|shower chair|raised toilet seat|"
                           r"blood pressure cuff|wheelchair|hospital bed)\b", r"\bhome health\b",
                           r"\btransportation\b"]),
    ("interpreter_services", [r"\binterpreter\b", r"\btranslate\b", r"\btranslator\b"]),
    ("forms_letters", [r"\bwork note\b", r"\bschool note\b", r"\bfmla\b", r"\bdisability form",
                       r"\bleave paperwork\b", r"\bpaperwork filled out\b", r"\bletter for\b"]),
    ("portal_support", [r"\blog ?in\b", r"\bsign in\b", r"\bpassword\b", r"\bpatient app\b",
                        r"\baccount is locked\b", r"\bportal (is|keeps|won)"]),
    ("patient_relations", [r"\bcomplaint\b", r"\bunhappy with\b", r"\brude\b", r"\bcompliment\b",
                           r"\bgrievance\b"]),
    ("outside_records", [r"\benclosed are visit records\b", r"\bconsult note from\b",
                         r"\bdischarge summary from\b", r"\brecords from [a-z ]+ for the patient\b"]),
    ("staff_messages", [r"\bheads-up for the team\b", r"\bteam huddle\b", r"\bfax line will be down\b",
                        r"\bsupply order\b", r"^fyi\b"]),
]
# Phrases that are request details / pleasantries / vague filler; they contribute no intent.
FILLER_PATTERNS = [
    r"^(thanks|thank you|appreciate it)", r"^pharmacy is\b", r"^please send it to\b", r"^same pharmacy\b",
    r"^i have about a week left", r"^running low", r"^i have not heard anything", r"^i have a question about",
    r"^who can help me get one ordered", r"^mornings the following week", r"^this is [a-z]+, [a-z]+'?s? ",
    r"^i am typing this for", r"^i am listed as an authorized proxy", r"^i am not sure whether i am set up",
    r"^please see below", r"^can someone take care of this", r"^following up on what",
    r"^just checking in about", r"^is this something you can handle", r"^form attached", r"^please review",
]

# Refill 'named medication' detection: dose pattern or a visible medication list.
MED_NAMES = ["albuterol", "amlodipine", "atorvastatin", "escitalopram", "fluticasone", "levothyroxine",
             "lisinopril", "losartan", "metformin", "montelukast", "omeprazole", "sertraline", "simvastatin",
             "tamsulosin", "gabapentin", "metoprolol", "hydrochlorothiazide", "insulin", "prednisone"]
DOSE_RE = r"\b\d+(\.\d+)? ?(mg|mcg|g|ml|units?)\b"
UNNAMED_MED_RE = r"\b(my medicine|my pills|my prescription|the usual one|my meds|my medication)\b"

PROXY_EXEMPT_ROLES = {"patient", "clinic staff", "staff", "pharmacy", "insurer", "outside facility",
                      "result interface"}

GREETING_RE = r"^(hello|hi there|hi|good mornings?|good afternoon|also|separately|one more thing:?|and)[,:]?\s+"


def build_vocab():
    words = set()
    for group in [SYMPTOM_PATTERNS, NEGATION_PATTERNS, FILLER_PATTERNS] + [p for _, p in INTENT_PATTERNS]:
        for p in group:
            for w in re.findall(r"[a-z]{5,}", re.sub(r"\\[a-zA-Z]|\[[^\]]*\]", " ", p)):
                words.add(w)
    for w in MED_NAMES + ["records", "record", "referral", "immunization", "authorization", "paperwork",
                          "appointment", "prescription", "statement", "complaint", "treated", "pharmacy",
                          "insurance", "requires", "delivered", "shower", "walker", "walked", "signed",
                          "verification", "instructions", "physical", "differential", "wording", "office",
                          "employer", "someone", "getting", "kicking", "patient", "account", "payment",
                          "balance", "printout", "through", "reschedule", "renewed", "script", "refill",
                          "select", "answer", "override", "reviewer", "nothing", "longer", "previous", "instructions",
                          "authorization", "details", "request", "escalate",
                          "never"]:
        # "never" added: the one-edit typo normalizer otherwise rewrites the common English word
        # "never" into "fever", misrouting negated-symptom portal messages (e.g. "the password
        # reset link never arrives"). See PLAN-PUBLIC-REPO R2.5.
        words.add(w)
    return words


VOCAB = build_vocab()
VOCAB_BY_LEN = collections.defaultdict(list)
for _w in VOCAB:
    VOCAB_BY_LEN[len(_w)].append(_w)


def typo_normalize(text):
    """Lowercase; replace each unknown word (>=5 letters) with the unique vocabulary word within one
    edit/adjacent transposition. Handles noisy-evidence typos like 'rfeill' -> 'refill'."""
    def fix(m):
        w = m.group(0)
        if len(w) < 5 or w in VOCAB:
            return w
        cands = set()
        for L in (len(w) - 1, len(w), len(w) + 1):
            for v in VOCAB_BY_LEN.get(L, ()):
                if damerau1(w, v):
                    cands.add(v)
        return cands.pop() if len(cands) == 1 else w
    return re.sub(r"[a-z]+", fix, text.lower())


def split_sentences(text):
    parts = re.split(r"(?<=[.?!])\s+|\s{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def strip_injections(text):
    found = []
    for p in INJECTION_PATTERNS:
        for m in re.finditer(p, text, re.I | re.S):
            found.append(m.group(0))
        text = re.sub(p, " ", text, flags=re.I | re.S)
    return text, found


def inbox_decide(ev):
    item = ev.get("item")
    if not isinstance(item, dict):
        return None, "legacy seed inbox format (no item/linked_chart/sender fields); rule parser not applicable"
    body_raw = str(item.get("body") or "")
    text = typo_normalize(norm_space(body_raw))
    text, injections = strip_injections(text)
    inj_note = " [embedded instruction ignored]" if injections else ""
    sender = item.get("from") or {}
    role = norm_space(sender.get("role") or "").lower()

    # Rule 1: symptom (negated statements removed first)
    sym_text = text
    for p in NEGATION_PATTERNS:
        sym_text = re.sub(p, " ", sym_text)
    for p in SYMPTOM_PATTERNS:
        m = re.search(p, sym_text)
        if m:
            return "nurse_triage", "inbox_rule_1_symptom: '%s'%s" % (m.group(0), inj_note)

    # Rule 2: identity (regarding_patient vs linked_chart)
    rp = ev.get("regarding_patient")
    lc = ev.get("linked_chart") or {}
    if isinstance(rp, dict):
        if present(rp.get("name")) and present(lc.get("name")) and not names_match(rp["name"], lc["name"]):
            return "needs_human_review", "inbox_rule_2_identity: regarding_patient name != linked_chart name"
        if present(rp.get("dob")) and present(lc.get("dob")) and not dates_equal(rp["dob"], lc["dob"]):
            return "needs_human_review", "inbox_rule_2_identity: regarding_patient DOB != linked_chart DOB"

    # Rule 3: proxy (account_flags authoritative; body claims ignored)
    if role and role not in PROXY_EXEMPT_ROLES:
        flags = ev.get("account_flags") or {}
        active = [p for p in (flags.get("proxy_access") or [])
                  if norm_space(p.get("status") or "").lower() == "active"
                  and names_match(p.get("name"), sender.get("name"), tolerance=0)]
        if not active:
            return "needs_human_review", "inbox_rule_3_proxy: sender role '%s' has no ACTIVE proxy_access entry" % role

    # Result interface: route by source-system result_info (rule 7 result branch)
    if role == "result interface":
        ri = ev.get("result_info") or {}
        sev = norm_space(ri.get("source_severity_flag") or "").lower()
        rtype = norm_space(ri.get("result_type") or "").lower()
        if sev == "critical":
            return "critical_results", "inbox_rule_7: result interface, source_severity_flag critical" + inj_note
        if "discrete" in rtype:
            return "results", "inbox_rule_7: result interface, discrete result" + inj_note
        if "narrative" in rtype or "note" in rtype:
            return "result_notes", "inbox_rule_7: result interface, narrative result note" + inj_note
        return "needs_human_review", "inbox_rule_4: result interface item without usable result_info"

    # Intent detection, sentence by sentence
    intents = collections.OrderedDict()  # dest -> list of sentences
    unparsed = []
    rx_named, rx_unnamed = False, False
    third_party_recipients, self_records = [], False
    for s in split_sentences(text):
        s = re.sub(r"^this is [a-z]+, [a-z]+'s [a-z]+; i am typing this for [a-z]+\.?\s*", "", s)
        s = re.sub(GREETING_RE, "", s).strip()
        if not s:
            continue
        hit = None
        for dest, pats in INTENT_PATTERNS:
            if any(re.search(p, s) for p in pats):
                hit = dest
                break
        if hit is None:
            if not any(re.search(p, s) for p in FILLER_PATTERNS):
                unparsed.append(s)
            continue
        intents.setdefault(hit, []).append(s)
        if hit == "rx_requests":
            if re.search(DOSE_RE, s) or any(m in s for m in MED_NAMES):
                rx_named = True
            else:  # e.g. UNNAMED_MED_RE: 'my medicine', 'my pills', 'the usual one'
                rx_unnamed = True
        if hit == "medical_records_roi":
            m = re.search(r"\b(?:send my records|fax my (?:visit notes|records))\b.*?\bto ([a-z0-9&' .-]+?)"
                          r"(?: before my appointment there)?[.?!]?$", s)
            if m and not re.search(r"\bthrough the portal\b|\bto me\b", s):
                third_party_recipients.append(m.group(1).strip())
            else:
                self_records = True

    # Sender-role-derived intents when the body alone is uninformative
    if not intents:
        if role in ("clinic staff", "staff") and unparsed:
            intents["staff_messages"] = unparsed
        elif role == "outside facility":
            intents["outside_records"] = ["outside facility document"]

    # Rule 4: no specific request
    if not intents:
        why = "only vague/filler text" if not unparsed else "no recognized request (%d unparsed sentence(s))" % len(unparsed)
        return "needs_human_review", "inbox_rule_4_no_request: %s%s" % (why, inj_note)

    # Rule 5: multi-intent
    if len(intents) >= 2:
        return "needs_human_review", "inbox_rule_5_multi_intent: %s%s" % ("+".join(intents), inj_note)

    dest = next(iter(intents))
    # Rule 6: completeness
    if dest == "rx_requests" and not rx_named:
        return "needs_human_review", "inbox_rule_6_incomplete: refill without a named medication"
    if dest == "medical_records_roi" and third_party_recipients:
        flags = ev.get("account_flags") or {}
        auths = [a for a in (flags.get("roi_authorizations") or [])
                 if norm_space(a.get("status") or "").lower() == "active"]
        for rcpt in third_party_recipients:
            if not any(loose(a.get("recipient") or "") == loose(rcpt) for a in auths):
                return "needs_human_review", "inbox_rule_6_incomplete: release to '%s' without active ROI authorization" % rcpt
    note = "" if not unparsed else " [%d unparsed sentence(s)]" % len(unparsed)
    return dest, "inbox_rule_7_destination: %s%s%s" % (dest, inj_note, note)


# ---------------------------------------------------------------------------
# RESULTS
# ---------------------------------------------------------------------------
# Rule 1 / 7 item_type classification (keyword lists, checked in this order).
RESULT_TYPE_KEYWORDS = [
    ("cardiology_results", ["cardiology", "echocardiogram", "echo", "ecg", "ekg", "holter", "stress test"]),
    ("imaging_results", ["imaging", "radiology", "x-ray", "xray", "mri", "ct ", "ultrasound", "mammogram"]),
    ("pathology_review", ["pathology", "cytology"]),
    ("microbiology_review", ["microbiology", "culture"]),
    ("result_notes", ["result note", "narrative", "interpretive", "result comment"]),
    ("results", ["lab", "laboratory", "discrete", "panel"]),
]


def classify_item_type(item_type):
    t = " " + norm_space(item_type or "").lower() + " "
    for dest, kws in RESULT_TYPE_KEYWORDS:
        if any(k in t for k in kws):
            return dest
    return None


def results_decide(ev):
    n = ev.get("notification") or {}
    if "synthetic_id" in n and "message_class" not in n:
        return results_decide_legacy(n)
    rtype = classify_item_type(n.get("item_type"))
    mclass = norm_space(n.get("message_class") or "").lower()
    if rtype is None or mclass != "result":
        return "needs_human_review", "results_rule_1_item_type: item_type=%r message_class=%r" % (
            n.get("item_type"), n.get("message_class"))
    pt = n.get("patient") or {}
    missing = [k for k in ("name", "mrn", "dob") if not present(pt.get(k))]
    if missing:
        return "needs_human_review", "results_rule_2_identity: missing patient %s" % "/".join(missing)
    sf = n.get("source_flag")
    if not present(sf):
        return "needs_human_review", "results_rule_3a_missing_flag"
    sf = norm_space(sf).lower()
    banner = n.get("notification_banner")
    if present(banner) and norm_space(banner).lower() != sf:
        return "needs_human_review", "results_rule_3b_flag_conflict: banner=%s source_flag=%s" % (
            norm_space(banner).lower(), sf)
    if sf == "critical":
        return "critical_results", "results_rule_4_critical"
    status = n.get("result_status")
    if not present(status):
        return "needs_human_review", "results_rule_5a_missing_status"
    status = norm_space(status).lower()
    cos = n.get("requires_cosign")
    if cos is True or (isinstance(cos, str) and cos.strip().lower() == "true"):
        return "cosign_pending", "results_rule_5b_cosign"
    if status in ("corrected", "amended"):
        return "corrected_results", "results_rule_5c_corrected"
    if status == "preliminary":
        return "preliminary_results", "results_rule_5d_preliminary"
    if norm_space(n.get("performing_org_type") or "").lower() == "outside":
        return "outside_results", "results_rule_6_outside"
    return rtype, "results_rule_7_type: %r -> %s" % (n.get("item_type"), rtype)


def results_decide_legacy(n):
    """Seed result records: item_type + severity_flag + source_severity + routing_completeness only.
    Free-text 'evidence' is never used. Both flags must be present and equal (rule 3)."""
    rtype = classify_item_type(n.get("item_type"))
    if rtype not in ("results", "result_notes"):
        return "needs_human_review", "results_rule_1_item_type (legacy): %r" % n.get("item_type")
    if norm_space(n.get("routing_completeness") or "").lower() != "complete":
        return "needs_human_review", "results_rule_2/5a (legacy): routing_completeness not complete"
    a, b = n.get("severity_flag"), n.get("source_severity")
    if not present(a) or not present(b):
        return "needs_human_review", "results_rule_3a (legacy): a severity flag is missing"
    if norm_space(a).lower() != norm_space(b).lower():
        return "needs_human_review", "results_rule_3b (legacy): severity_flag != source_severity"
    if norm_space(b).lower() == "critical":
        return "critical_results", "results_rule_4_critical (legacy)"
    return rtype, "results_rule_7_type (legacy): %s" % rtype


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def decide(rec, policy):
    wf = rec["workflow"]
    ev = rec.get("evidence") or {}
    options = rec.get("options") or {}
    dests = policy["workflows"][wf]["destinations"]
    if wf == "chart":
        d, why = chart_decide(ev)
        return chart_finalize(d, why, options, dests)
    if wf == "inbox":
        d, why = inbox_decide(ev)
    elif wf == "results":
        d, why = results_decide(ev)
    else:
        return None, "unknown workflow"
    return finalize(d, why, options, dests)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bank", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--extra-vocab", default=None, metavar="PATH",
                    help="file of extra known words (one per line) that the typo normalizer must never rewrite; used for bank v2 (data/v2/vocab.txt)")
    ap.add_argument("--summary", default=None, help="default: rule_summary.json next to --out")
    a = ap.parse_args(argv)
    if a.extra_vocab:
        with open(a.extra_vocab, encoding="utf-8") as fh:
            VOCAB.update(w.strip().lower() for w in fh if len(w.strip()) >= 5)

    with open(a.policy, "r", encoding="utf-8") as fh:
        policy = json.load(fh)
    recs = load_bank(a.bank)

    per_wf = collections.defaultdict(lambda: {"n": 0, "decided": 0, "null": 0,
                                               "abstain": 0, "choices": collections.Counter(),
                                               "rules": collections.Counter()})
    with open(a.out, "w", encoding="utf-8") as out:
        for rec in recs:
            choice, reason = decide(rec, policy)
            if choice is not None and choice not in rec["options"]:  # hard safety check
                raise SystemExit("BUG: %s produced non-option %r" % (rec["case_id"], choice))
            out.write(json.dumps({"case_id": rec["case_id"], "rule_choice": choice, "rule_reason": reason},
                                 ensure_ascii=False) + "\n")
            s = per_wf[rec["workflow"]]
            s["n"] += 1
            if choice is None:
                s["null"] += 1
                s["choices"]["<null>"] += 1
            else:
                s["decided"] += 1
                if choice in ABSTAIN_KEYS:
                    s["abstain"] += 1
                s["choices"][choice if not rec["workflow"] == "chart" or choice in ABSTAIN_KEYS
                             else "<chart id>"] += 1
            s["rules"][re.split(r"[:( ]", reason, 1)[0]] += 1

    summary = {"n_cases": len(recs), "note": "counts only; no accuracy computed (label-blind)",
               "workflows": {}}
    tot = collections.Counter()
    for wf, s in sorted(per_wf.items()):
        summary["workflows"][wf] = {
            "n": s["n"], "decided": s["decided"], "null": s["null"],
            "decided_abstain": s["abstain"], "decided_route": s["decided"] - s["abstain"],
            "choice_distribution": dict(s["choices"].most_common()),
            "rule_fired_distribution": dict(s["rules"].most_common()),
        }
        tot.update({"n": s["n"], "decided": s["decided"], "null": s["null"], "abstain": s["abstain"]})
    summary["totals"] = dict(tot)
    spath = a.summary or os.path.join(os.path.dirname(os.path.abspath(a.out)), "rule_summary.json")
    with open(spath, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=False)
        fh.write("\n")
    print(json.dumps({"out": a.out, "summary": spath, "totals": dict(tot)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
