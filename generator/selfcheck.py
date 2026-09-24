#!/usr/bin/env python3
"""Self-check for bank.jsonl / plan.json / policy.json (SPEC §1-§3). Pure stdlib.

Usage: python3 selfcheck.py [--dir <fixtures dir>]
Besides schema/structure checks, it re-derives chart and results labels from the rendered EVIDENCE ONLY
(independent parser, no generator facts) to confirm rendering fidelity. Inbox evidence is free text, so
only its structured parts (proxy/identity/result-flag) are cross-checked.
"""
import argparse
import collections
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OVERNIGHT = os.path.dirname(HERE)
EHR = os.path.dirname(OVERNIGHT)
HR = "needs_human_review"
PERT_ENUM = {
    "n_options": {2, 4, 8, 16}, "abstain_available": {True, False}, "evidence_style": {"concise", "expanded", "noisy"},
    "field_condition": {"ordinary", "near_miss", "missing", "conflicting"}, "distractors": {True, False},
    "option_order": {"canonical", "shuffled"}, "synonyms": {True, False}, "authority_conflict": {True, False},
    "injection": {True, False}, "decision_shape": {"single_step", "multi_field"}}
KEYS = ["case_id", "workflow", "split", "stratum", "family_id", "counterfactual_of", "changed_fact", "perturbations",
        "evidence", "options", "expected", "label_basis", "ambiguity", "repeat_panel"]
LEAKS = ["expected", "label_basis", "stratum", "counterfactual", "ground truth", "ground_truth", "correct answer",
         "near_miss", "near miss", "field_condition", "_rule_", "abstain", "irreducible", "historical_seed",
         "no_valid_option", "label:", "decision_shape"]
PHASES = ["pilot", "broad", "counterfactual", "repeat", "load_c1", "load_c2", "load_c4", "load_c8", "seed", "holdout"]
MON = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

errors = []


def err(msg):
    errors.append(msg)


# ---------------- independent evidence-only verifiers ----------------
def parse_date(s):
    s = s.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return datetime.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return datetime.date(int(m[3]), int(m[1]), int(m[2]))
    m = re.fullmatch(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", s)
    if m:
        return datetime.date(int(m[3]), MON[m[1]], int(m[2]))
    raise ValueError("bad date %r" % s)


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def nm(s):
    s = s.strip()
    if "," in s:
        a, b = s.split(",", 1)
        s = b.strip() + " " + a.strip()
    t = [re.sub(r"[^a-z]", "", x.lower()) for x in s.split()]
    t = [x for x in t if len(x) > 1]
    return t[0], t[-1]


def name_ok(a, b):
    (fa, la), (fb, lb) = nm(a), nm(b)
    return lev(fa, fb) + lev(la, lb) <= 1


def pick(d, *names):
    for n in names:
        if n in d:
            return d[n]
    return None


def verify_chart(ev):
    r = ev["request"]
    req = {"name": pick(r, "patient_name", "name_on_request"), "mrn": pick(r, "mrn", "medical_record_number"),
           "dob": pick(r, "dob", "date_of_birth"), "phone": pick(r, "phone", "callback_number"),
           "email": pick(r, "email", "email_address"), "address": pick(r, "address", "home_address")}
    cands = []
    for c in ev["candidates"]:
        cands.append({"id": c["chart_id"], "name": pick(c, "name", "registered_name"), "dob": pick(c, "dob", "date_of_birth"),
                      "phone": pick(c, "phone", "phone_on_file"), "email": pick(c, "email", "email_on_file"),
                      "address": pick(c, "address", "address_on_file")})

    def norm(k, v):
        if v is None:
            return None
        if k == "dob":
            return parse_date(v)
        if k == "phone":
            return re.sub(r"\D", "", v)
        return re.sub(r"[^a-z0-9]", "", v.lower())
    if req["mrn"] is not None:
        m = [c for c in cands if c["id"] == req["mrn"].strip()]
        if not m:
            return HR
        c = m[0]
        if req["dob"] is not None and norm("dob", req["dob"]) != norm("dob", c["dob"]):
            return HR
        if not name_ok(req["name"], c["name"]):
            return HR
        return c["id"]
    sup = [k for k in ("dob", "phone", "email", "address") if req[k] is not None]
    q = [c for c in cands if name_ok(req["name"], c["name"]) and len(sup) >= 2
         and all(norm(k, req[k]) == norm(k, c[k]) for k in sup)]
    return q[0]["id"] if len(q) == 1 else HR


def res_cat(item_type):
    t = item_type.lower()
    if t in ("patient call", "refill request", "appointment request", "staff message", "billing notice",
             "order requiring signature", "portal message"):
        return None
    if "imag" in t or "radiology" in t:
        return "imaging_results"
    if "patholog" in t or "cytology" in t:
        return "pathology_review"
    if "micro" in t or "culture" in t:
        return "microbiology_review"
    if "cardio" in t or "ecg" in t or "echo" in t:
        return "cardiology_results"
    if "note" in t or "comment" in t:
        return "result_notes"
    if "lab" in t:
        return "results"
    raise ValueError("unknown item_type %r" % item_type)


def verify_results(ev):
    n = ev["notification"]
    cat = res_cat(n["item_type"])
    if cat is None or n.get("message_class") != "Result":
        return HR
    p = n.get("patient", {})
    if not all(isinstance(p.get(k), str) and p.get(k).strip() for k in ("name", "mrn", "dob")):
        return HR
    sf = n.get("source_flag")
    if sf is None:
        return HR
    b = n.get("notification_banner")
    if b is not None and b.lower() != sf.lower():
        return HR
    if sf.lower() == "critical":
        return "critical_results"
    st = n.get("result_status")
    if st is None:
        return HR
    if n["requires_cosign"] is True:
        return "cosign_pending"
    if st in ("Corrected", "Amended"):
        return "corrected_results"
    if st == "Preliminary":
        return "preliminary_results"
    if n["performing_org_type"] == "outside":
        return "outside_results"
    return cat


def inbox_structural(ev, expected, basis):
    """Cross-check the structured preconditions for rule-based labels."""
    frm = ev["item"]["from"]
    if basis == "inbox_rule_3_proxy":
        if frm["role"] != "family member":
            return "proxy rule but sender not family member"
        if any(p["name"] == frm["name"] and p["status"] == "active" for p in ev.get("account_flags", {}).get("proxy_access", [])):
            return "proxy rule but active entry present"
    if frm["role"] == "family member" and basis not in ("inbox_rule_1_symptom", "inbox_rule_2_identity", "inbox_rule_3_proxy"):
        if not any(p["name"] == frm["name"] and p["status"] == "active" for p in ev.get("account_flags", {}).get("proxy_access", [])):
            return "family sender without active proxy but label not proxy/earlier rule"
    if basis == "inbox_rule_2_identity":
        rp, lc = ev["regarding_patient"], ev["linked_chart"]
        if parse_date(rp["dob"]) == parse_date(lc["dob"]) and name_ok(rp["name"], lc["name"]):
            return "identity rule but regarding_patient matches"
    elif "regarding_patient" in ev and basis != "inbox_rule_1_symptom":
        rp, lc = ev["regarding_patient"], ev["linked_chart"]
        if not (parse_date(rp["dob"]) == parse_date(lc["dob"]) and name_ok(rp["name"], lc["name"])):
            return "regarding_patient mismatch but identity rule did not fire"
    ri = ev.get("result_info")
    if ri and basis == "inbox_rule_7_destination":
        want = "critical_results" if ri["source_severity_flag"] == "critical" else ("results" if ri["result_type"] == "discrete" else "result_notes")
        if want != expected:
            return "result_info implies %s" % want
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=HERE)
    ap.add_argument("--bank", default=None, help="path to bank.jsonl; overrides <dir>/bank.jsonl")
    ap.add_argument("--plan", default=None, help="path to plan json; overrides <dir>/plans/plan_jev-qwen.json or <dir>/plan.json")
    a = ap.parse_args()
    policy = json.load(open(os.path.join(OVERNIGHT, "policy", "policy.json")))
    assert policy["policy_version"] == "v1"
    for wf in ("chart", "inbox", "results"):
        w = policy["workflows"][wf]
        for k in ("policy_text", "destinations", "abstain_key", "rules"):
            if k not in w:
                err("policy %s missing %s" % (wf, k))
        if w["abstain_key"] != HR:
            err("policy abstain_key")
        if len(w["policy_text"]) > 1300:
            err("policy_text too long %s" % wf)
    if len(policy["workflows"]["inbox"]["destinations"]) - 1 < 16 or len(policy["workflows"]["results"]["destinations"]) - 1 < 16:
        err("catalog < 16")

    cases = [json.loads(l) for l in open(a.bank or os.path.join(a.dir, "bank.jsonl"), encoding="utf-8")]
    byid = {}
    for c in cases:
        if list(c.keys()) != KEYS:
            err("%s key order/schema %s" % (c.get("case_id"), list(c.keys())))
        if c["case_id"] in byid:
            err("duplicate id %s" % c["case_id"])
        byid[c["case_id"]] = c
    seeds_src = {}
    seed_files_present = all(os.path.exists(os.path.join(EHR, d, "cases.json")) for d in ("chart", "inbox", "results"))
    if seed_files_present:
        for d in ("chart", "inbox", "results"):
            for s in json.load(open(os.path.join(EHR, d, "cases.json"))):
                seeds_src["seed-" + s["id"]] = (d, s)
    n_seed = 0
    stratum_counts = collections.Counter()
    ver = collections.Counter()
    for c in cases:
        cid, wf, P = c["case_id"], c["workflow"], c["perturbations"]
        if wf not in ("chart", "inbox", "results"):
            err("%s workflow" % cid)
        if c["split"] not in ("dev", "holdout", "seed"):
            err("%s split" % cid)
        for k, allowed in PERT_ENUM.items():
            if k not in P:
                err("%s missing pert %s" % (cid, k))
            elif k == "n_options" and c["split"] == "seed":
                pass
            elif P[k] not in allowed:
                err("%s pert %s=%r" % (cid, k, P[k]))
        if P.get("n_options") != len(c["options"]):
            err("%s n_options %s != %d" % (cid, P.get("n_options"), len(c["options"])))
        abst_key = "abstain" if (c["split"] == "seed" and wf == "chart") else HR
        if P["abstain_available"] != (abst_key in c["options"]):
            err("%s abstain_available mismatch" % cid)
        amb = c["ambiguity"]
        if amb is not None and amb.get("kind") not in ("irreducible", "no_valid_option", "adjudication_disagreement"):
            err("%s ambiguity kind" % cid)
        if amb and amb["kind"] == "no_valid_option":
            if c["expected"] != HR or HR in c["options"] or P["abstain_available"]:
                err("%s no_valid_option shape" % cid)
        else:
            if c["expected"] not in c["options"]:
                err("%s expected not in options" % cid)
            if not P["abstain_available"] and c["expected"] == abst_key:
                err("%s abstain label without abstain option" % cid)
        if not isinstance(c["repeat_panel"], bool):
            err("%s repeat_panel type" % cid)
        if c["split"] == "seed":
            n_seed += 1
            d, s = seeds_src.get(cid, (None, None))
            if s is None or s["input"] != c["evidence"] or list(s["options"].items()) != list(c["options"].items()) \
                    or s["expected"] != c["expected"] or c["stratum"] != "historical_seed" or d != wf:
                err("%s seed not verbatim" % cid)
            continue
        stratum_counts[(wf, c["stratum"])] += 1
        if c["counterfactual_of"] is None:
            if c["changed_fact"] is not None:
                err("%s base has changed_fact" % cid)
        else:
            p = byid.get(c["counterfactual_of"])
            if p is None:
                err("%s parent missing" % cid)
            else:
                if p["counterfactual_of"] is not None or p["family_id"] != c["family_id"] or p["split"] != c["split"] or p["workflow"] != wf:
                    err("%s parent relation" % cid)
                if list(p["options"]) != list(c["options"]):
                    err("%s sibling options differ from base" % cid)
            if not (isinstance(c["changed_fact"], str) and c["changed_fact"].strip()) or ";" in c["changed_fact"]:
                err("%s changed_fact must be exactly one description" % cid)
        blob = json.dumps(c["evidence"], ensure_ascii=False).lower()
        for L in LEAKS:
            if L in blob:
                err("%s leak %r" % (cid, L))
        for k in c["options"]:
            if any(x in c["options"][k].lower() for x in ("correct answer", "correct option", "expected", "(safe)")):
                err("%s option description leak" % cid)
        # independent verification
        if amb is None:
            if wf == "chart":
                got = verify_chart(c["evidence"])
                ver[("chart", got == c["expected"])] += 1
                if got != c["expected"]:
                    err("%s chart verifier %s != %s" % (cid, got, c["expected"]))
            elif wf == "results":
                got = verify_results(c["evidence"])
                ver[("results", got == c["expected"])] += 1
                if got != c["expected"]:
                    err("%s results verifier %s != %s" % (cid, got, c["expected"]))
            else:
                e = inbox_structural(c["evidence"], c["expected"], c["label_basis"])
                ver[("inbox_structural", e is None)] += 1
                if e:
                    err("%s inbox structural: %s" % (cid, e))
    if seed_files_present and n_seed != 54:
        err("seed count %d" % n_seed)
    for k, v in sorted(stratum_counts.items()):
        if v < 50:
            err("stratum %s has %d < 50" % (k, v))
    # family / split checks
    fam_split = {}
    for c in cases:
        if c["split"] == "seed":
            continue
        s = fam_split.setdefault(c["family_id"], set())
        s.add(c["split"])
    if any(len(s) > 1 for s in fam_split.values()):
        err("family split across dev/holdout")
    per_wf = collections.Counter()
    for c in cases:
        if c["split"] != "seed":
            per_wf[(c["workflow"], c["split"])] += 1
    for wf in ("chart", "inbox", "results"):
        h = per_wf[(wf, "holdout")] / (per_wf[(wf, "holdout")] + per_wf[(wf, "dev")])
        if not 0.15 <= h <= 0.25:
            err("holdout share %s %.3f" % (wf, h))
        bases = set(c["counterfactual_of"] for c in cases if c["workflow"] == wf and c["counterfactual_of"]
                    and not c["changed_fact"].startswith("presentation only"))
        if len(bases) < 150:
            err("%s counterfactual bases %d < 150" % (wf, len(bases)))
        gen = [c for c in cases if c["workflow"] == wf and c["split"] != "seed"]
        irr = sum(1 for c in gen if c["ambiguity"] and c["ambiguity"]["kind"] == "irreducible")
        if irr > 0.05 * len(gen):
            err("irreducible > 5%% in %s" % wf)
        lab = [c for c in gen if c["ambiguity"] is None]
        na = sum(1 for c in lab if c["expected"] != HR) / len(lab)
        if not 0.45 <= na <= 0.61:
            err("%s non-abstain share %.3f outside target" % (wf, na))
    rp = [c for c in cases if c["repeat_panel"]]
    if len(rp) != 120 or any(c["split"] != "dev" for c in rp) or collections.Counter(c["workflow"] for c in rp) != {"chart": 40, "inbox": 40, "results": 40}:
        err("repeat panel shape")
    # plan
    base = os.path.dirname(os.path.abspath(a.bank)) if a.bank else a.dir
    plan_path = a.plan or (os.path.join(base, "plans", "plan_jev-qwen.json")
                           if os.path.exists(os.path.join(base, "plans", "plan_jev-qwen.json"))
                           else os.path.join(base, "plan.json"))
    plan = json.load(open(plan_path))
    if plan.get("plan_version") != "v1" or plan.get("order_seed") != 20260923:
        err("plan header")
    tr = plan["trials"]
    if len(tr) > 9400:
        err("too many trials %d" % len(tr))
    ids = [t["trial_id"] for t in tr]
    if len(ids) != len(set(ids)):
        err("dup trial ids")
    ph = collections.Counter()
    dev_primary = collections.Counter()
    for t in tr:
        c = byid.get(t["case_id"])
        if c is None:
            err("trial case missing %s" % t["trial_id"])
            continue
        if t["trial_id"] != "%s#%s#r%d" % (t["case_id"], t["phase"], t["rep"]):
            err("trial id format %s" % t["trial_id"])
        if t["phase"] not in PHASES or sorted(t["arm_order"]) != ["jev", "qwen"]:
            err("trial phase/arm %s" % t["trial_id"])
        ph[t["phase"]] += 1
        if (t["phase"] == "holdout") != (c["split"] == "holdout"):
            err("holdout leakage %s" % t["trial_id"])
        if (t["phase"] == "seed") != (c["split"] == "seed"):
            err("seed phase %s" % t["trial_id"])
        if t["phase"] in ("pilot", "broad", "counterfactual"):
            dev_primary[t["case_id"]] += 1
            if t["rep"] != 0:
                err("primary rep %s" % t["trial_id"])
        if t["phase"] == "counterfactual" and c["counterfactual_of"] is None:
            err("base duplicated in counterfactual phase %s" % t["trial_id"])
        if t["phase"] == "repeat" and (t["rep"] not in (1, 2, 3) or not c["repeat_panel"]):
            err("repeat trial %s" % t["trial_id"])
        if t["phase"].startswith("load_") and t["rep"] != {"load_c1": 10, "load_c2": 11, "load_c4": 12, "load_c8": 13}[t["phase"]]:
            err("load rep %s" % t["trial_id"])
    for c in cases:
        if c["split"] == "dev" and dev_primary[c["case_id"]] != 1:
            err("dev case %s has %d primary trials" % (c["case_id"], dev_primary[c["case_id"]]))
        if c["repeat_panel"] and not any(t["case_id"] == c["case_id"] and t["phase"] == "broad" for t in tr):
            err("repeat case without broad trial %s" % c["case_id"])
    if ph["pilot"] != 90 or ph["repeat"] != 360 or ("seed" in ph and ph["seed"] != 54) or any(ph[p] != 150 for p in ("load_c1", "load_c2", "load_c4", "load_c8")):
        err("phase sizes %s" % dict(ph))
    print(json.dumps({"cases": len(cases), "trials": len(tr), "phases": dict(ph),
                      "independent_verification": {"%s:%s" % k: v for k, v in sorted(ver.items())},
                      "errors": len(errors)}, indent=1))
    for e in errors[:60]:
        print("ERROR", e)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
