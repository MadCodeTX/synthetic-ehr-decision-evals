#!/usr/bin/env python3
"""Independent verifier for a paired Qwen vs Jev run (SPEC.md v1).

Trusts nothing the runner summarised: every hash, validity decision, correctness
flag, spend total and metric is recomputed from raw files (bank, plan, policy,
manifest, attempts/results/pairs/events JSONL). Python 3.9 stdlib only, no network.

Usage:
  verify_paired.py <run_dir> --bank bank.jsonl --plan plan.json --policy policy.json
                   [--out verification.json] [--expected-qwen-id Qwen/Qwen3.8-27B-FP8]
                   [--max-usd 5] [--max-trials 10000] [--deadline ISO] [--strict-harness]
Exit code 0 iff every hard check passes.
"""
import argparse
import csv
import datetime as _dt
import hashlib
import json
import math
import pathlib
import random
import re
import sys
from collections import Counter, defaultdict

ARMS = ("jev", "qwen")
LOAD_PHASES = {"load_c1": 1, "load_c2": 2, "load_c4": 4, "load_c8": 8}
STOP_EVENT_TYPES = {"stop_requested", "signal", "drift", "deadline"}
STOP_GRACE_S = 2.0
LATENCY_TOL_MS = 50.0
MONOTONE_TOL_MS = 1000.0
# Fallback only when the manifest carries no deadline at all: matches the runner default (now + 24 h).
DEFAULT_DEADLINE = None
BOOT_SEED = 20260923
BOOT_N = 1000
Z95 = 1.959963984540054
SEED_ABSTAIN_KEY = "abstain"   # seed chart cases keep their original abstain key
MAX_DETAIL = 25
# JS String.prototype.trim() whitespace + line terminators (differs from Python str.strip()).
JS_WS = ("\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004"
         "\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")


# ---------------------------------------------------------------- primitives
def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    return sha256_bytes(pathlib.Path(path).read_bytes())


def norm_hash(h):
    if not isinstance(h, str):
        return None
    h = h.strip().lower()
    return h[7:] if h.startswith("sha256:") else h


def canon(x):
    """SPEC §4 Python equivalent of canon()."""
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def options_pairs(options):
    return [[k, v] for k, v in options.items()]


def input_hash(workflow, evidence, options, policy_text):
    obj = {"workflow": workflow, "evidence": evidence,
           "options_pairs": options_pairs(options), "policy_text": policy_text}
    return sha256_bytes(canon(obj).encode("utf-8"))


def _js_num(v):
    """Number formatting as JS JSON.stringify would do it (diagnostic only)."""
    if isinstance(v, bool) or not isinstance(v, float):
        return None
    if v.is_integer() and abs(v) < 1e21:
        return int(v)
    return None


def _js_normalise(x):
    if isinstance(x, dict):
        return {k: _js_normalise(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_js_normalise(v) for v in x]
    n = _js_num(x)
    return n if n is not None else x


def input_hash_js_variant(workflow, evidence, options, policy_text):
    """Same as input_hash but with integral floats rendered like JS (1.0 -> 1)."""
    return input_hash(workflow, _js_normalise(evidence), options, policy_text)


def is_finite_unit(v):
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and 0.0 <= v <= 1.0)


def js_trim(s):
    return s.strip(JS_WS)


def _reject_constant(name):
    raise ValueError("non-JSON constant %s" % name)


_TS_FRAC = re.compile(r"^(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$")


def parse_ts(s):
    """ISO-8601 -> epoch seconds (float) or None."""
    if not isinstance(s, str):
        return None
    m = _TS_FRAC.match(s.strip())
    if not m:
        return None
    base, frac, tz = m.groups()
    frac = (frac or "0")[:6].ljust(6, "0")
    tz = tz or "Z"
    if tz == "Z":
        tz = "+00:00"
    elif ":" not in tz:
        tz = tz[:3] + ":" + tz[3:]
    try:
        d = _dt.datetime.fromisoformat("%s.%s%s" % (base, frac, tz))
    except ValueError:
        return None
    return d.timestamp()


def iso(t):
    if t is None:
        return None
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------- statistics
def wilson(k, n, z=Z95):
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [max(0.0, centre - half), min(1.0, centre + half)]


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value (binomial on discordant pairs, p=0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def percentile(values, p):
    """Nearest-rank percentile."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    idx = max(0, math.ceil(p / 100.0 * len(vals)) - 1)
    return vals[idx]


def summary(values):
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v)]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "mean": sum(vals) / len(vals), "p50": percentile(vals, 50),
            "p95": percentile(vals, 95), "p99": percentile(vals, 99),
            "min": min(vals), "max": max(vals)}


def bootstrap_diff(diffs, seed, n_boot=BOOT_N):
    if not diffs:
        return None
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return {"estimate": sum(diffs) / n, "ci95": [means[int(0.025 * n_boot)],
                                                 means[int(0.975 * n_boot) - 1]],
            "resamples": n_boot, "seed": seed}


def rate(k, n):
    return (k / n) if n else None


# ---------------------------------------------------------------- validation (§6)
def validate_jev(raw, option_keys):
    out = {"valid": False, "choice": None, "failure_reason": None, "probabilities": None,
           "confidence": None, "chosen_probability": None, "raw_choice": None}
    ans = raw.get("answers") if isinstance(raw, dict) else None
    ch = ans.get("choice") if isinstance(ans, dict) else None
    if not isinstance(ch, dict):
        out["failure_reason"] = "missing answers.choice"
        return out
    out["raw_choice"] = ch.get("choice")
    if ch.get("type") != "choice":
        out["failure_reason"] = "type != choice"
        return out
    c = ch.get("choice")
    if not isinstance(c, str) or c not in option_keys:
        out["failure_reason"] = "choice not a supplied key"
        return out
    probs = ch.get("probabilities")
    if not isinstance(probs, dict):
        out["failure_reason"] = "probabilities missing"
        return out
    if set(probs.keys()) != set(option_keys):
        out["failure_reason"] = "probability key set != option key set"
        return out
    if not all(is_finite_unit(v) for v in probs.values()):
        out["failure_reason"] = "probability not finite in [0,1]"
        return out
    conf = ch.get("confidence")
    if not is_finite_unit(conf):
        out["failure_reason"] = "confidence not finite in [0,1]"
        return out
    out.update(valid=True, choice=c, probabilities=probs, confidence=conf,
               chosen_probability=probs[c])
    return out


def qwen_content(raw):
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("content"), str):
        return raw["content"]
    try:
        c = raw["choices"][0]["message"]["content"]
        return c if isinstance(c, str) else None
    except (KeyError, IndexError, TypeError):
        return None


def validate_qwen(content, option_keys):
    out = {"valid": False, "choice": None, "failure_reason": None}
    if not isinstance(content, str):
        out["failure_reason"] = "no message content"
        return out
    try:
        obj = json.loads(js_trim(content), parse_constant=_reject_constant)
    except ValueError:
        out["failure_reason"] = "content is not strict JSON"
        return out
    if not isinstance(obj, dict) or list(obj.keys()) != ["choice"]:
        out["failure_reason"] = "not an object with exactly key 'choice'"
        return out
    v = obj["choice"]
    if not isinstance(v, str) or v not in option_keys:
        out["failure_reason"] = "choice not a supplied key"
        return out
    out.update(valid=True, choice=v)
    return out


_LENIENT = re.compile(r'"choice"\s*:\s*"((?:[^"\\]|\\.)*)"')


def lenient_choice(content, option_keys):
    if not isinstance(content, str):
        return None
    for m in _LENIENT.finditer(content):
        try:
            k = json.loads('"%s"' % m.group(1))
        except ValueError:
            k = m.group(1)
        if k in option_keys:
            return k
    return None


# ---------------------------------------------------------------- io helpers
def read_jsonl(path):
    rows, errors = [], []
    p = pathlib.Path(path)
    if not p.exists():
        return rows, ["missing file %s" % p.name]
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as e:
            errors.append("%s:%d %s" % (p.name, i, e))
            continue
        if not isinstance(obj, dict):
            errors.append("%s:%d not an object" % (p.name, i))
            continue
        obj["__line"] = i
        rows.append(obj)
    return rows, errors


def deep_find(obj, names, path=()):
    """Breadth-first search for the first key in `names`; returns (path, value)."""
    queue = [(obj, path)]
    while queue:
        cur, pth = queue.pop(0)
        if isinstance(cur, dict):
            for n in names:
                if n in cur and cur[n] is not None:
                    return ".".join(pth + (n,)), cur[n]
            for k, v in cur.items():
                if isinstance(v, (dict, list)):
                    queue.append((v, pth + (str(k),)))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                if isinstance(v, (dict, list)):
                    queue.append((v, pth + (str(i),)))
    return None, None


def harness_entries(manifest):
    _, v = deep_find(manifest, ["harness_files", "harness_sha256", "harness_hashes",
                                "harness_file_sha256s", "harness"])
    out = []
    if isinstance(v, dict):
        for k, h in v.items():
            if isinstance(h, str):
                out.append((k, h))
            elif isinstance(h, dict):
                out.append((h.get("path", k), h.get("sha256") or h.get("hash")))
    elif isinstance(v, list):
        for e in v:
            if isinstance(e, dict):
                out.append((e.get("path") or e.get("file"), e.get("sha256") or e.get("hash")))
    return out


def resolve_path(p, run_dir):
    cand = pathlib.Path(p)
    if cand.is_absolute():
        return cand if cand.exists() else None
    roots = [run_dir] + list(run_dir.parents)[:5] + [pathlib.Path.cwd()] + \
        list(pathlib.Path(__file__).resolve().parents)[:4]
    for r in roots:
        q = r / p
        if q.exists():
            return q
    return None


def ev_type(e):
    for k in ("type", "event", "kind", "name"):
        if isinstance(e.get(k), str):
            return e[k]
    return None


def ev_time(e):
    for k in ("t_utc", "ts_utc", "t", "ts", "time", "timestamp", "at", "t_start_utc"):
        t = parse_ts(e.get(k))
        if t is not None:
            return t
    return None


# ---------------------------------------------------------------- report
class Report:
    def __init__(self):
        self.checks = []

    def check(self, name, ok, detail=None, hard=True):
        self.checks.append({"name": name, "pass": bool(ok), "hard": hard, "detail": detail})
        return ok

    def items(self, name, failures, total, hard=True, extra=None):
        detail = {"checked": total, "failures": len(failures), "examples": failures[:MAX_DETAIL]}
        if extra:
            detail.update(extra)
        return self.check(name, not failures, detail, hard)

    @property
    def passed(self):
        return all(c["pass"] for c in self.checks if c["hard"])


# ---------------------------------------------------------------- main verify
def verify(run_dir, bank_path, plan_path, policy_path, expected_qwen_id="Qwen/Qwen3.8-27B-FP8",
           max_usd=5.0, max_trials=10000, deadline=None, strict_harness=False):
    """Returns (verification_dict, metrics_dict, csv_rows)."""
    run = pathlib.Path(run_dir).resolve()
    R = Report()

    # ---------- 1. fixtures + manifest hashes
    manifest_p = run / "manifest.json"
    manifest = {}
    if R.check("manifest.json present and parseable", manifest_p.exists(), str(manifest_p)):
        try:
            manifest = json.loads(manifest_p.read_text(encoding="utf-8"))
        except ValueError as e:
            R.check("manifest.json present and parseable", False, str(e))
    actual = {"policy": sha256_file(policy_path), "fixture": sha256_file(bank_path),
              "plan": sha256_file(plan_path)}
    names = {"policy": ["policy_hash", "policy_sha256"],
             "fixture": ["fixture_hash", "fixture_sha256", "bank_hash", "bank_sha256"],
             "plan": ["plan_hash", "plan_sha256"]}
    for kind in ("policy", "fixture", "plan"):
        where, rec = deep_find(manifest, names[kind])
        R.check("%s hash matches manifest" % kind, norm_hash(rec) == actual[kind],
                {"recomputed": actual[kind], "manifest": rec, "manifest_key": where})

    h_entries = harness_entries(manifest)
    h_res = {"matched": [], "mismatched": [], "missing": []}
    for p, h in h_entries:
        rp = resolve_path(p, run) if p else None
        if rp is None:
            h_res["missing"].append(p)
        elif norm_hash(h) == sha256_file(rp):
            h_res["matched"].append(p)
        else:
            h_res["mismatched"].append({"path": p, "resolved": str(rp), "manifest": h,
                                        "recomputed": sha256_file(rp)})
    R.check("harness file hashes match manifest (files that exist)",
            not h_res["mismatched"] and bool(h_entries),
            dict(h_res, listed=len(h_entries)), hard=strict_harness)

    # ---------- load fixtures
    policy = json.loads(pathlib.Path(policy_path).read_text(encoding="utf-8"))
    wf_policy = policy.get("workflows", {})
    bank_rows, bank_err = read_jsonl(bank_path)
    plan = json.loads(pathlib.Path(plan_path).read_text(encoding="utf-8"))
    R.check("bank parses", not bank_err, bank_err[:MAX_DETAIL])
    bank, dups = {}, []
    for c in bank_rows:
        if c.get("case_id") in bank:
            dups.append(c.get("case_id"))
        bank[c.get("case_id")] = c
    R.items("bank case_ids unique", dups, len(bank_rows))
    bad_label = [c["case_id"] for c in bank_rows
                 if not isinstance(c.get("options"), dict)
                 or (c.get("expected") not in c["options"]
                     and (c.get("ambiguity") or {}).get("kind") != "no_valid_option")
                 or c.get("workflow") not in wf_policy]
    R.items("bank labels are option keys and workflows exist in policy", bad_label, len(bank_rows))

    trials = plan.get("trials", [])
    plan_by, pdups, pmissing = {}, [], []
    for t in trials:
        if t.get("trial_id") in plan_by:
            pdups.append(t.get("trial_id"))
        plan_by[t.get("trial_id")] = t
        if t.get("case_id") not in bank:
            pmissing.append(t.get("trial_id"))
    R.items("plan trial_ids unique", pdups, len(trials))
    R.items("plan trials reference bank cases", pmissing, len(trials))
    R.check("plan size <= max trials", len(trials) <= max_trials,
            {"plan_trials": len(trials), "max_trials": max_trials}, hard=False)

    # ---------- load run files
    attempts, a_err = read_jsonl(run / "attempts.jsonl")
    results, r_err = read_jsonl(run / "results.jsonl")
    pairs, p_err = read_jsonl(run / "pairs.jsonl")
    events, e_err = read_jsonl(run / "events.jsonl")
    R.check("run JSONL files parse", not (a_err + r_err + p_err),
            (a_err + r_err + p_err)[:MAX_DETAIL])
    R.check("events.jsonl parses", not e_err, e_err[:MAX_DETAIL], hard=False)

    # ---------- manifest: model pin, deadline
    pin_where, pinned = deep_find(manifest, ["pinned_qwen_id", "pinned_qwen_model", "qwen_pinned_model",
                                             "pinned_model", "pinned_model_id", "qwen_model_id",
                                             "pinned_id"])
    if pinned is None and isinstance(manifest.get("arms"), dict):
        q = manifest["arms"].get("qwen") or {}
        pinned, pin_where = q.get("model"), "arms.qwen.model"
    R.check("manifest pinned qwen id == expected", pinned == expected_qwen_id,
            {"manifest": pinned, "manifest_key": pin_where, "expected": expected_qwen_id})
    if deadline is None:
        _, deadline = deep_find(manifest, ["deadline_utc", "resolved_deadline_utc",
                                           "resolved_deadline", "deadline", "hard_stop_utc"])
        if isinstance(deadline, dict):
            deadline = deadline.get("utc") or deadline.get("iso")
    deadline_t = parse_ts(deadline) if deadline else None
    if deadline_t is None and manifest.get("created_utc"):
        deadline_t = parse_ts(manifest["created_utc"]) + 24 * 3600
        deadline = manifest["created_utc"]

    # attempts index
    att_by_key = defaultdict(list)
    att_ids = Counter()
    for a in attempts:
        att_ids[a.get("attempt_id")] += 1
        att_by_key[(a.get("trial_id"), a.get("arm"), a.get("segment"))].append(a)
    R.items("attempt_ids unique", [k for k, n in att_ids.items() if n > 1], len(attempts))

    # ---------- 3. per-result recomputation
    res_by_id, rdups = {}, []
    for r in results:
        if r.get("result_id") in res_by_id:
            rdups.append(r.get("result_id"))
        res_by_id[r.get("result_id")] = r
    R.items("result_ids unique", rdups, len(results))

    F = defaultdict(list)   # failure lists keyed by check name
    rec = {}                # result_id -> recomputed record
    hash_hints = []
    for r in results:
        rid = r.get("result_id")
        tag = {"result_id": rid, "trial_id": r.get("trial_id"), "arm": r.get("arm")}
        pt = plan_by.get(r.get("trial_id"))
        if pt is None:
            F["results reference planned trials"].append(tag)
        elif (r.get("case_id"), r.get("phase"), r.get("rep")) != \
                (pt.get("case_id"), pt.get("phase"), pt.get("rep")):
            F["result case/phase/rep match plan"].append(tag)
        if r.get("arm") not in ARMS:
            F["result arm is jev|qwen"].append(tag)
            continue
        case = bank.get(r.get("case_id"))
        if case is None:
            F["results reference bank cases"].append(tag)
            continue
        wf = case.get("workflow")
        wpol = wf_policy.get(wf, {})
        options = case.get("options", {})
        keys = list(options.keys())
        if (r.get("workflow"), r.get("stratum")) != (wf, case.get("stratum")):
            F["result workflow/stratum match bank"].append(tag)
        ih = input_hash(wf, case.get("evidence"), options, wpol.get("policy_text"))
        if norm_hash(r.get("input_hash")) != ih:
            F["result input_hash matches recomputation"].append(dict(tag, recomputed=ih,
                                                                     recorded=r.get("input_hash")))
            if norm_hash(r.get("input_hash")) == input_hash_js_variant(
                    wf, case.get("evidence"), options, wpol.get("policy_text")):
                hash_hints.append(rid)
        ropts = r.get("options")
        ropts = [list(p) for p in ropts] if isinstance(ropts, list) else ropts
        if ropts != options_pairs(options):
            F["result options equal bank options (keys, text, order)"].append(tag)
        if r.get("expected") != case.get("expected"):
            F["result expected equals bank label"].append(dict(tag, recorded=r.get("expected"),
                                                               bank=case.get("expected")))
        raw = r.get("raw_answer")
        lc = None
        if r["arm"] == "jev":
            v = validate_jev(raw, keys)
        else:
            content = qwen_content(raw)
            v = validate_qwen(content, keys)
            lc = lenient_choice(content, keys)
            if r.get("lenient_choice") != lc:
                F["qwen lenient_choice matches recomputation"].append(
                    dict(tag, recorded=r.get("lenient_choice"), recomputed=lc))
        if r.get("valid") is not v["valid"]:
            F["result validity matches independent re-validation"].append(
                dict(tag, recorded=r.get("valid"), recomputed=v["valid"], reason=v["failure_reason"]))
        elif v["valid"] and r.get("choice") != v["choice"]:
            F["result choice matches raw answer"].append(dict(tag, recorded=r.get("choice"),
                                                              recomputed=v["choice"]))
        if not v["valid"] and r.get("choice") is not None:
            F["invalid results record choice=null"].append(dict(tag, recorded=r.get("choice")))
        if not v["valid"] and not r.get("failure_reason"):
            F["invalid results carry failure_reason"].append(tag)
        if r["arm"] == "jev" and v["valid"]:
            ok = (r.get("probabilities") == v["probabilities"] and r.get("confidence") == v["confidence"]
                  and r.get("chosen_probability") == v["chosen_probability"])
            if not ok:
                F["jev recorded probabilities/confidence match raw"].append(tag)
        abstain_keys = {k for k in (wpol.get("abstain_key"), SEED_ABSTAIN_KEY) if k in options}
        correct = bool(v["valid"] and v["choice"] == case.get("expected"))
        abstained = bool(v["valid"] and v["choice"] in abstain_keys)
        if r.get("correct") is not correct:
            F["result correct matches recomputation"].append(dict(tag, recorded=r.get("correct"),
                                                                  recomputed=correct))
        if r.get("abstained") is not abstained:
            F["result abstained matches recomputation" if v["valid"]
              else "invalid result abstained flag"].append(tag)
        if r["arm"] == "qwen" and (r.get("reported_cost_usd") is not None or r.get("cost_known")):
            F["qwen cost recorded as null/unknown (never 0)"].append(
                dict(tag, reported_cost_usd=r.get("reported_cost_usd"), cost_known=r.get("cost_known")))
        n_att = len(att_by_key.get((r.get("trial_id"), r["arm"], r.get("segment")), []))
        if r.get("attempts") != n_att:
            F["results.attempts equals attempt lines"].append(dict(tag, recorded=r.get("attempts"),
                                                                   lines=n_att))
        elif isinstance(r.get("retries"), int) and r["retries"] != n_att - 1:
            F["results.retries == attempts-1"].append(tag)
        rec[rid] = {
            "result_id": rid, "trial_id": r.get("trial_id"), "case_id": r.get("case_id"),
            "arm": r["arm"], "phase": r.get("phase"), "rep": r.get("rep"), "workflow": wf,
            "stratum": case.get("stratum"), "segment": r.get("segment"),
            "expected": case.get("expected"), "valid": v["valid"], "choice": v["choice"],
            "correct": correct, "abstained": abstained, "confidence": v.get("confidence"),
            "chosen_p": v.get("chosen_probability"), "lenient": lc,
            "ambiguous": case.get("ambiguity") is not None, "input_hash": ih,
            "latency_total": r.get("latency_ms_total"), "latency_final": r.get("latency_ms_final"),
            "t_start": parse_ts(r.get("t_start_utc")), "t_end": parse_ts(r.get("t_end_utc")),
            "concurrency": r.get("concurrency"), "tok_s": r.get("e2e_completion_tok_per_s"),
            "completion_tokens": r.get("completion_tokens"), "api_model": r.get("api_model"),
            "request_model": r.get("request_model"), "prompt_hash": r.get("prompt_hash"),
        }
    hard_result_checks = [
        "results reference planned trials", "result case/phase/rep match plan", "result arm is jev|qwen",
        "results reference bank cases", "result workflow/stratum match bank",
        "result input_hash matches recomputation",
        "result options equal bank options (keys, text, order)", "result expected equals bank label",
        "result validity matches independent re-validation", "result choice matches raw answer",
        "jev recorded probabilities/confidence match raw", "result correct matches recomputation",
        "result abstained matches recomputation", "qwen cost recorded as null/unknown (never 0)",
        "results.attempts equals attempt lines"]
    soft_result_checks = ["qwen lenient_choice matches recomputation", "invalid results record choice=null",
                          "invalid results carry failure_reason", "invalid result abstained flag",
                          "results.retries == attempts-1"]
    for name in hard_result_checks:
        extra = {"js_number_format_would_match": hash_hints[:MAX_DETAIL]} \
            if name.startswith("result input_hash") and hash_hints else None
        R.items(name, F[name], len(results), hard=True, extra=extra)
    for name in soft_result_checks:
        R.items(name, F[name], len(results), hard=False)

    # prompt_hash invariance: identical input -> identical prompt per arm
    ph = defaultdict(set)
    for x in rec.values():
        ph[(x["arm"], x["input_hash"])].add(x["prompt_hash"])
    R.items("prompt_hash constant per (arm, input_hash)",
            [{"arm": k[0], "input_hash": k[1], "prompt_hashes": sorted(map(str, v))}
             for k, v in ph.items() if len(v) > 1], len(ph), hard=False)

    # ---------- 2. pairs
    seen, pdup = set(), []
    ref_count = Counter()
    PF = defaultdict(list)
    counted = []   # (pair, jev_rec, qwen_rec)
    for p in pairs:
        tid = p.get("trial_id")
        tag = {"trial_id": tid}
        if tid in seen:
            pdup.append(tid)
            continue
        seen.add(tid)
        pt = plan_by.get(tid)
        if pt is None:
            PF["pair trial_id in plan"].append(tag)
            continue
        if pt.get("case_id") not in bank or p.get("case_id") != pt.get("case_id"):
            PF["pair case exists and matches plan"].append(tag)
            continue
        if (p.get("phase"), p.get("rep")) != (pt.get("phase"), pt.get("rep")):
            PF["pair phase/rep match plan"].append(tag)
        if list(p.get("arm_order") or []) != list(pt.get("arm_order") or []):
            PF["pair arm_order matches plan"].append(dict(tag, pair=p.get("arm_order"),
                                                          plan=pt.get("arm_order")))
        ok = True
        links = {}
        for arm in ARMS:
            rid = p.get("%s_result_id" % arm)
            ref_count[rid] += 1
            r = res_by_id.get(rid)
            if r is None or rid not in rec:
                PF["pair result_ids exist"].append(dict(tag, arm=arm, result_id=rid))
                ok = False
            elif r.get("trial_id") != tid or r.get("arm") != arm:
                PF["pair results belong to trial and correct arm"].append(dict(tag, arm=arm))
                ok = False
            else:
                links[arm] = rec[rid]
                if p.get("%s_choice" % arm) != r.get("choice"):
                    PF["pair choices equal result choices"].append(dict(tag, arm=arm))
        case = bank[pt["case_id"]]
        if p.get("expected") != case.get("expected"):
            PF["pair expected equals bank label"].append(tag)
        wpol = wf_policy.get(case.get("workflow"), {})
        if norm_hash(p.get("input_hash")) != input_hash(case.get("workflow"), case.get("evidence"),
                                                        case.get("options", {}), wpol.get("policy_text")):
            PF["pair input_hash matches recomputation"].append(tag)
        if ok:
            if p.get("agree") is not (p.get("jev_choice") == p.get("qwen_choice")):
                PF["pair agree flag consistent"].append(tag)
            counted.append((p, links["jev"], links["qwen"]))
    R.items("pairs trial_ids unique (no duplicate pair)", pdup, len(pairs))
    for name in ["pair trial_id in plan", "pair case exists and matches plan", "pair phase/rep match plan",
                 "pair arm_order matches plan", "pair result_ids exist",
                 "pair results belong to trial and correct arm", "pair choices equal result choices",
                 "pair expected equals bank label", "pair input_hash matches recomputation"]:
        R.items(name, PF[name], len(pairs))
    R.items("pair agree flag consistent", PF["pair agree flag consistent"], len(pairs), hard=False)
    R.items("no result referenced by two pairs", [k for k, n in ref_count.items() if n > 1],
            len(ref_count))
    orphans = [r for r in results if r.get("result_id") not in ref_count]
    orph_by_seg = Counter(str(r.get("segment")) for r in orphans)
    R.check("orphan results (not referenced by a pair; excluded)", not orphans,
            {"count": len(orphans), "by_segment": dict(orph_by_seg),
             "examples": [r.get("result_id") for r in orphans[:MAX_DETAIL]]}, hard=False)
    counted_trials = {p["trial_id"] for p, _, _ in counted}
    counted_ids = {x["result_id"] for _, j, q in counted for x in (j, q)}

    # ---------- 4. model identity
    q_bad_counted, q_bad_other, q_req_bad = [], [], []
    jev_models = Counter()
    jev_models_counted = Counter()
    for x in rec.values():
        if x["arm"] == "qwen":
            if x["api_model"] != pinned:
                (q_bad_counted if x["result_id"] in counted_ids else q_bad_other).append(
                    {"result_id": x["result_id"], "api_model": x["api_model"]})
            if x["request_model"] != pinned:
                q_req_bad.append({"result_id": x["result_id"], "request_model": x["request_model"]})
        else:
            jev_models[x["api_model"]] += 1
            if x["result_id"] in counted_ids:
                jev_models_counted[x["api_model"]] += 1
    qa_bad_counted, qa_bad_other = [], []
    for a in attempts:
        if a.get("arm") == "qwen" and a.get("api_model") is not None and a.get("api_model") != pinned:
            (qa_bad_counted if a.get("trial_id") in counted_trials else qa_bad_other).append(
                {"attempt_id": a.get("attempt_id"), "api_model": a.get("api_model")})
    R.items("qwen api_model == pinned id (counted results)", q_bad_counted,
            sum(1 for x in rec.values() if x["arm"] == "qwen"))
    R.items("qwen attempt api_model == pinned id (counted trials)", qa_bad_counted,
            sum(1 for a in attempts if a.get("arm") == "qwen"))
    R.items("qwen request_model == pinned id", q_req_bad, len(rec))
    R.check("qwen model mismatch outside counted data", not (q_bad_other or qa_bad_other),
            {"results": q_bad_other[:MAX_DETAIL], "attempts": qa_bad_other[:MAX_DETAIL]}, hard=False)
    jev_attempt_models = Counter(str(a.get("api_model")) for a in attempts
                                 if a.get("arm") == "jev" and a.get("api_model") is not None)
    R.check("jev api_model consistent in counted results", len(jev_models_counted) <= 1,
            {"counted_distinct": {str(k): v for k, v in jev_models_counted.items()},
             "all_results_distinct": {str(k): v for k, v in jev_models.items() if v},
             "attempt_distinct": dict(jev_attempt_models)})
    drift_events = [e for e in events if ev_type(e) == "drift"]
    R.check("no drift events", not drift_events,
            [{k: v for k, v in e.items() if k != "__line"} for e in drift_events[:MAX_DETAIL]], hard=False)

    # ---------- 5. spend / trial caps
    spent_reported, spent_unknown, n_unknown = 0.0, 0.0, 0
    bad_reserve, bad_cost, q_cost_bad = [], [], []
    for a in attempts:
        aid = a.get("attempt_id")
        if a.get("arm") == "jev":
            res_usd = a.get("reserved_usd")
            if not (isinstance(res_usd, (int, float)) and not isinstance(res_usd, bool)
                    and math.isfinite(res_usd) and res_usd > 0):
                bad_reserve.append(aid)
                res_usd = 0.0
            c = a.get("reported_cost_usd")
            finite = isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c >= 0
            if a.get("cost_known") and finite:
                spent_reported += c
            else:
                if a.get("cost_known"):
                    bad_cost.append(aid)
                spent_unknown += res_usd
                n_unknown += 1
        elif a.get("arm") == "qwen":
            if a.get("reported_cost_usd") is not None or a.get("cost_known"):
                q_cost_bad.append(aid)
    total_spend = spent_reported + spent_unknown
    R.items("every jev attempt reserved_usd > 0", bad_reserve,
            sum(1 for a in attempts if a.get("arm") == "jev"))
    R.items("jev cost_known implies finite reported cost >= 0", bad_cost,
            sum(1 for a in attempts if a.get("arm") == "jev"))
    R.items("qwen attempt cost null/unknown (never 0)", q_cost_bad,
            sum(1 for a in attempts if a.get("arm") == "qwen"))
    R.check("openrouter spend (reported + unknown reservations) <= cap", total_spend <= max_usd + 1e-12,
            {"reported_usd": round(spent_reported, 8), "unknown_charged_usd": round(spent_unknown, 8),
             "unknown_attempts": n_unknown, "total_usd": round(total_spend, 8), "cap_usd": max_usd})
    R.check("unknown-cost jev attempts", n_unknown == 0,
            {"count": n_unknown, "charged_usd": round(spent_unknown, 8)}, hard=False)
    admitted = {a.get("trial_id") for a in attempts}
    R.check("trials admitted <= max trials", len(admitted) <= max_trials,
            {"admitted": len(admitted), "max_trials": max_trials})
    R.items("attempts reference planned trials", [a.get("attempt_id") for a in attempts
                                                  if a.get("trial_id") not in plan_by], len(attempts))

    # ---------- 6. stop behaviour / deadline
    seg_starts = sorted((ev_time(e), e.get("segment")) for e in events
                        if ev_type(e) == "segment_start" and ev_time(e) is not None)
    seg_first = {}
    for a in attempts:
        t = parse_ts(a.get("t_start_utc"))
        if t is not None:
            s = a.get("segment")
            seg_first[s] = min(t, seg_first.get(s, t))
    stops = [e for e in events if ev_type(e) in STOP_EVENT_TYPES and ev_time(e) is not None]
    after_stop = []
    for e in stops:
        ts = ev_time(e)
        if "segment" in e:
            segs = {e.get("segment")}
        else:
            prior = [s for t, s in seg_starts if t <= ts]
            segs = {prior[-1]} if prior else {s for s, t in seg_first.items() if t <= ts}
        for a in attempts:
            t = parse_ts(a.get("t_start_utc"))
            if a.get("segment") in segs and t is not None and t > ts + STOP_GRACE_S:
                after_stop.append({"attempt_id": a.get("attempt_id"), "t_start_utc": a.get("t_start_utc"),
                                   "stop_event": ev_type(e), "stop_t": iso(ts)})
    R.items("no attempt started after a stop event (+2s grace, same segment)", after_stop, len(attempts),
            extra={"stop_events": len(stops)})
    run_end = [e for e in events if ev_type(e) == "run_end"]
    R.check("run_end event present", bool(run_end),
            {"stop_events": [ev_type(e) for e in stops], "run_end_events": len(run_end)}, hard=False)
    late = [a.get("attempt_id") for a in attempts
            if (parse_ts(a.get("t_start_utc")) or 0) > deadline_t + STOP_GRACE_S]
    R.items("no attempt started after deadline", late, len(attempts), extra={"deadline": deadline})
    incomplete = sorted(t for t in admitted if t not in seen)
    R.check("incomplete trials (attempts without pair)", not incomplete,
            {"count": len(incomplete), "examples": incomplete[:MAX_DETAIL]}, hard=False)

    # ---------- 7. timestamps
    lat_bad, neg, mono = [], [], []
    last_end = {}
    for a in attempts:
        t0, t1 = parse_ts(a.get("t_start_utc")), parse_ts(a.get("t_end_utc"))
        if t0 is None or t1 is None:
            neg.append({"attempt_id": a.get("attempt_id"), "why": "unparseable timestamp"})
            continue
        if t1 < t0:
            neg.append({"attempt_id": a.get("attempt_id"), "why": "t_end < t_start"})
        lm = a.get("latency_ms")
        if not isinstance(lm, (int, float)) or abs(lm - (t1 - t0) * 1000.0) > LATENCY_TOL_MS:
            lat_bad.append({"attempt_id": a.get("attempt_id"), "latency_ms": lm,
                            "span_ms": round((t1 - t0) * 1000.0, 1)})
        s = a.get("segment")
        if s in last_end and t1 * 1000.0 < last_end[s] - MONOTONE_TOL_MS:
            mono.append({"attempt_id": a.get("attempt_id"), "segment": s})
        last_end[s] = max(last_end.get(s, t1 * 1000.0), t1 * 1000.0)
    R.items("attempt timestamps parse and t_end >= t_start", neg, len(attempts), hard=False)
    R.items("attempt latency_ms == t_end - t_start (+-50ms)", lat_bad, len(attempts), hard=False)
    R.items("attempt t_end monotone-ish per segment (1s tolerance)", mono, len(attempts), hard=False)

    metrics, csv_rows = compute_metrics(rec, counted, bank, attempts, results, spent_reported,
                                        spent_unknown, n_unknown)
    verification = {
        "verifier": "verify/verify_paired.py",
        "generated_utc": iso(_dt.datetime.now(_dt.timezone.utc).timestamp()),
        "run_dir": str(run), "pass": R.passed,
        "hard_failures": [c["name"] for c in R.checks if c["hard"] and not c["pass"]],
        "soft_findings": [c["name"] for c in R.checks if not c["hard"] and not c["pass"]],
        "inputs": {"bank": str(bank_path), "plan": str(plan_path), "policy": str(policy_path),
                   "hashes": actual, "expected_qwen_id": expected_qwen_id, "pinned_qwen_id": pinned,
                   "max_usd": max_usd, "max_trials": max_trials, "deadline": deadline},
        "counts": {"bank_cases": len(bank), "plan_trials": len(trials), "attempts": len(attempts),
                   "results": len(results), "pairs": len(pairs), "counted_pairs": len(counted),
                   "orphans": len(orphans), "incomplete_trials": len(incomplete),
                   "admitted_trials": len(admitted), "events": len(events)},
        "checks": R.checks,
    }
    return verification, metrics, csv_rows


# ---------------------------------------------------------------- 8. metrics
def compute_metrics(rec, counted, bank, attempts, results, spent_reported, spent_unknown, n_unknown):
    counted_recs = [x for _, j, q in counted for x in (j, q)]
    na = [x for x in counted_recs if not x["ambiguous"]]
    M = {"notes": [
        "All metrics recomputed from raw answers; recorded valid/correct flags are not used.",
        "Accuracy metrics use counted (paired) results on non-ambiguous cases only; invalid answers count as wrong.",
        "Latency/throughput include ambiguous cases; throughput uses all results (incl. orphans) in the phase.",
        "Qwen API fee is null (local; hardware/energy unmeasured), never $0.",
        "Pooled 'ALL' rows mix phases, so a case can appear more than once (repeat/load phases)."],
        "counts": {"counted_pairs": len(counted), "counted_results": len(counted_recs),
                   "non_ambiguous_results": len(na),
                   "ambiguous_results_excluded": len(counted_recs) - len(na)}}

    # accuracy table
    groups = defaultdict(list)
    for x in na:
        for wf in (x["workflow"], "ALL"):
            for st in (x["stratum"], "ALL"):
                for ph in (x["phase"], "ALL"):
                    groups[(x["arm"], wf, st, ph)].append(x)
    rows = []
    for (arm, wf, st, ph), xs in sorted(groups.items(), key=lambda kv: tuple(map(str, kv[0]))):
        n = len(xs)
        k = sum(x["correct"] for x in xs)
        v = sum(x["valid"] for x in xs)
        ab = sum(x["abstained"] for x in xs)
        ci = wilson(k, n)
        rows.append({"arm": arm, "workflow": wf, "stratum": st, "phase": ph, "n": n,
                     "valid_rate": rate(v, n), "accuracy": rate(k, n),
                     "acc_ci95_lo": ci[0] if ci else None, "acc_ci95_hi": ci[1] if ci else None,
                     "abstention_rate": rate(ab, n)})
    M["accuracy_by_arm_workflow_stratum_phase"] = rows

    # selective accuracy (jev)
    thresholds = [round(i * 0.05, 2) for i in range(21)]
    sel = {}
    jev_na = [x for x in na if x["arm"] == "jev"]
    for wf in sorted({x["workflow"] for x in jev_na}) + ["ALL"]:
        xs = [x for x in jev_na if wf == "ALL" or x["workflow"] == wf]
        sel[wf] = {}
        for score in ("confidence", "chosen_p"):
            curve = []
            for t in thresholds:
                cov = [x for x in xs if x["valid"] and x[score] is not None and x[score] >= t - 1e-12]
                curve.append({"threshold": t, "n": len(xs), "covered": len(cov),
                              "coverage": rate(len(cov), len(xs)),
                              "selective_accuracy": rate(sum(x["correct"] for x in cov), len(cov))})
            sel[wf]["chosen_probability" if score == "chosen_p" else "confidence"] = curve
    M["jev_selective_accuracy"] = sel

    # paired contingency + McNemar + bootstrap
    npairs = [(p, j, q) for p, j, q in counted if not j["ambiguous"]]
    paired = {}
    wfs = sorted({j["workflow"] for _, j, _ in npairs})
    for i, wf in enumerate(wfs + ["ALL"]):
        sub = [(j, q) for _, j, q in npairs if wf == "ALL" or j["workflow"] == wf]
        both = sum(j["correct"] and q["correct"] for j, q in sub)
        jonly = sum(j["correct"] and not q["correct"] for j, q in sub)
        qonly = sum(q["correct"] and not j["correct"] for j, q in sub)
        neither = len(sub) - both - jonly - qonly
        by_phase = {}
        for ph in sorted({j["phase"] for j, _ in sub}):
            s2 = [(j, q) for j, q in sub if j["phase"] == ph]
            b = sum(j["correct"] and not q["correct"] for j, q in s2)
            c = sum(q["correct"] and not j["correct"] for j, q in s2)
            by_phase[ph] = {"n": len(s2), "jev_only": b, "qwen_only": c, "mcnemar_p": mcnemar_exact(b, c)}
        paired[wf] = {"n": len(sub), "both_right": both, "jev_only": jonly, "qwen_only": qonly,
                      "both_wrong": neither, "mcnemar_exact_p": mcnemar_exact(jonly, qonly),
                      "choice_agreement": rate(sum(j["choice"] == q["choice"] for j, q in sub), len(sub)),
                      "accuracy_diff_jev_minus_qwen_bootstrap": bootstrap_diff(
                          [int(j["correct"]) - int(q["correct"]) for j, q in sub], BOOT_SEED + i),
                      "by_phase": by_phase}
    M["paired"] = paired

    # stability across repeat panel
    stab = {}
    for arm in ARMS:
        by_case = defaultdict(dict)
        for x in counted_recs:
            if x["arm"] == arm and x["phase"] == "repeat":
                by_case[x["case_id"]][x["rep"]] = x
        for x in counted_recs:   # rep 0 fallback from broad when repeat phase lacks it
            if x["arm"] == arm and x["phase"] == "broad" and x["rep"] == 0 and x["case_id"] in by_case \
                    and 0 not in by_case[x["case_id"]]:
                by_case[x["case_id"]][0] = x
        out = {}
        for wf in sorted({bank[c]["workflow"] for c in by_case}) + ["ALL"]:
            cs = [c for c in by_case if (wf == "ALL" or bank[c]["workflow"] == wf) and len(by_case[c]) >= 2]
            ident = sum(len({(x["choice"] if x["valid"] else "<invalid>") for x in by_case[c].values()}) == 1
                        for c in cs)
            out[wf] = {"cases": len(cs), "cases_with_4_reps": sum(len(by_case[c]) >= 4 for c in cs),
                       "identical_choice_fraction": rate(ident, len(cs))}
        stab[arm] = out
    M["repeat_stability"] = stab

    # counterfactual flip sensitivity
    pref = ["counterfactual", "broad", "pilot", "seed", "holdout", "repeat"]
    pick = defaultdict(dict)
    for x in counted_recs:
        if x["phase"] in LOAD_PHASES:
            continue
        cur = pick[x["arm"]].get(x["case_id"])
        key = (pref.index(x["phase"]) if x["phase"] in pref else 99, x["rep"] or 0)
        if cur is None or key < cur[0]:
            pick[x["arm"]][x["case_id"]] = (key, x)
    cf = {}
    for arm in ARMS:
        agg = defaultdict(lambda: Counter())
        for c in bank.values():
            o = c.get("counterfactual_of")
            if not o or o not in bank or c.get("ambiguity") is not None or bank[o].get("ambiguity") is not None:
                continue
            xc, xo = pick[arm].get(c["case_id"]), pick[arm].get(o)
            if not xc or not xo:
                continue
            xc, xo = xc[1], xo[1]
            flipped_label = c["expected"] != bank[o]["expected"]
            for wf in (c["workflow"], "ALL"):
                g = agg[wf]
                if flipped_label:
                    g["label_flipped_pairs"] += 1
                    g["followed_flip"] += int(xc["correct"] and xo["correct"])
                    g["choice_changed"] += int(xc["valid"] and xo["valid"] and xc["choice"] != xo["choice"])
                else:
                    g["label_same_pairs"] += 1
                    g["stayed_same"] += int(xc["valid"] and xo["valid"] and xc["choice"] == xo["choice"])
        cf[arm] = {wf: dict(g, flip_sensitivity=rate(g["followed_flip"], g["label_flipped_pairs"]),
                            invariance=rate(g["stayed_same"], g["label_same_pairs"]))
                   for wf, g in agg.items()}
    M["counterfactual"] = cf

    # overconfident errors
    oc = [x for x in jev_na if x["valid"] and not x["correct"] and (x["chosen_p"] or 0) >= 0.9]
    errs = [x for x in jev_na if not x["correct"]]
    M["jev_overconfident_errors"] = {
        "threshold": 0.9, "count": len(oc), "rate_of_all": rate(len(oc), len(jev_na)),
        "rate_of_errors": rate(len(oc), len(errs)),
        "by_workflow": dict(Counter(x["workflow"] for x in oc)),
        "examples": [{"trial_id": x["trial_id"], "case_id": x["case_id"], "expected": x["expected"],
                      "choice": x["choice"], "chosen_probability": x["chosen_p"]} for x in oc[:50]]}

    # latency
    lat = {}
    for arm in ARMS:
        xs = [x for x in counted_recs if x["arm"] == arm]
        lat[arm] = {"by_phase": {ph: {"total": summary([x["latency_total"] for x in xs if x["phase"] == ph]),
                                      "final": summary([x["latency_final"] for x in xs if x["phase"] == ph])}
                                 for ph in sorted({x["phase"] for x in xs})},
                    "by_concurrency": {str(c): summary([x["latency_total"] for x in xs
                                                        if LOAD_PHASES.get(x["phase"]) == c])
                                       for c in sorted(set(LOAD_PHASES.values()))}}
    M["latency_ms"] = lat

    # throughput per load phase
    all_rec = list(rec.values())
    thr = {}
    for ph, c in sorted(LOAD_PHASES.items(), key=lambda kv: kv[1]):
        xs = [x for x in all_rec if x["phase"] == ph]
        if not xs:
            continue
        span = 0.0
        for seg in {x["segment"] for x in xs}:
            ts = [x["t_start"] for x in xs if x["segment"] == seg and x["t_start"] is not None]
            te = [x["t_end"] for x in xs if x["segment"] == seg and x["t_end"] is not None]
            if ts and te:
                span += max(te) - min(ts)
        entry = {"concurrency": c, "wall_span_s": span}
        for arm in ARMS:
            ax = [x for x in xs if x["arm"] == arm]
            nv = sum(x["valid"] for x in ax)
            entry[arm] = {"n_results": len(ax), "n_valid": nv, "valid_rate": rate(nv, len(ax)),
                          "valid_per_min": (nv / (span / 60.0)) if span > 0 else None,
                          "results_per_min": (len(ax) / (span / 60.0)) if span > 0 else None}
        thr[ph] = entry
    M["throughput_load_phases"] = thr

    # qwen tok/s
    qx = [x for x in counted_recs if x["arm"] == "qwen"]
    recomputed = []
    mism = 0
    for x in qx:
        ct, lf = x["completion_tokens"], x["latency_final"]
        if isinstance(ct, (int, float)) and isinstance(lf, (int, float)) and lf > 0:
            v = ct / (lf / 1000.0)
            recomputed.append(v)
            if isinstance(x["tok_s"], (int, float)) and abs(x["tok_s"] - v) > 0.01 * max(1.0, v):
                mism += 1
    M["qwen_e2e_tok_per_s"] = {"recorded": summary([x["tok_s"] for x in qx]),
                               "recomputed_completion_tokens_over_final_latency": summary(recomputed),
                               "recorded_vs_recomputed_mismatches_gt1pct": mism}

    # jev cost
    att_cost = defaultdict(lambda: [0.0, 0.0, 0])   # (trial, segment) -> reported, unknown, n_unknown
    for a in attempts:
        if a.get("arm") != "jev":
            continue
        k = (a.get("trial_id"), a.get("segment"))
        c = a.get("reported_cost_usd")
        if a.get("cost_known") and isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c):
            att_cost[k][0] += c
        else:
            att_cost[k][1] += a.get("reserved_usd") or 0.0
            att_cost[k][2] += 1
    cost = {}
    for wf in sorted({x["workflow"] for x in jev_na}) + ["ALL"]:
        xs = [x for x in jev_na if wf == "ALL" or x["workflow"] == wf]
        rep = sum(att_cost[(x["trial_id"], x["segment"])][0] for x in xs)
        unk = sum(att_cost[(x["trial_id"], x["segment"])][1] for x in xs)
        nunk = sum(att_cost[(x["trial_id"], x["segment"])][2] for x in xs)
        nv, nc = sum(x["valid"] for x in xs), sum(x["correct"] for x in xs)
        cost[wf] = {"n": len(xs), "reported_usd": rep, "unknown_attempts": nunk, "unknown_reserved_usd": unk,
                    "usd_per_valid_reported_only": rep / nv if nv else None,
                    "usd_per_correct_reported_only": rep / nc if nc else None}
    M["jev_cost"] = {"by_workflow_non_ambiguous_counted": cost,
                     "run_total": {"reported_usd": spent_reported, "unknown_charged_usd": spent_unknown,
                                   "unknown_attempts": n_unknown},
                     "note": "per-decision $ uses reported cost only; unknown-cost attempts listed separately"}
    M["qwen_cost"] = "null (local; hardware/energy unmeasured)"

    # strict vs lenient
    ql = {}
    qna = [x for x in na if x["arm"] == "qwen"]
    for wf in sorted({x["workflow"] for x in qna}) + ["ALL"]:
        xs = [x for x in qna if wf == "ALL" or x["workflow"] == wf]
        ql[wf] = {"n": len(xs), "strict_valid_rate": rate(sum(x["valid"] for x in xs), len(xs)),
                  "lenient_valid_rate": rate(sum(x["lenient"] is not None for x in xs), len(xs)),
                  "strict_accuracy": rate(sum(x["correct"] for x in xs), len(xs)),
                  "lenient_accuracy": rate(sum(x["lenient"] == x["expected"] for x in xs), len(xs))}
    M["qwen_strict_vs_lenient"] = ql
    return M, rows


CSV_FIELDS = ["arm", "workflow", "stratum", "phase", "n", "valid_rate", "accuracy", "acc_ci95_lo",
              "acc_ci95_hi", "abstention_rate"]


def write_outputs(verification, metrics, rows, out_path):
    out = pathlib.Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(verification, indent=2, default=str) + "\n", encoding="utf-8")
    (out.parent / "metrics_verified.json").write_text(json.dumps(metrics, indent=2, default=str) + "\n",
                                                      encoding="utf-8")
    with open(out.parent / "metrics_by_stratum.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--bank", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--out")
    ap.add_argument("--expected-qwen-id", default="Qwen/Qwen3.8-27B-FP8")
    ap.add_argument("--max-usd", type=float, default=5.0)
    ap.add_argument("--max-trials", type=int, default=10000)
    ap.add_argument("--deadline", help="override deadline (ISO); default manifest, else %s" % DEFAULT_DEADLINE)
    ap.add_argument("--strict-harness", action="store_true",
                    help="treat harness-file hash mismatches as hard failures")
    a = ap.parse_args(argv)
    v, m, rows = verify(a.run_dir, a.bank, a.plan, a.policy, a.expected_qwen_id, a.max_usd, a.max_trials,
                        a.deadline, a.strict_harness)
    out = a.out or str(pathlib.Path(a.run_dir) / "verification.json")
    write_outputs(v, m, rows, out)
    print("verification %s: %d checks, hard failures: %s; soft findings: %s -> %s" % (
        "PASS" if v["pass"] else "FAIL", len(v["checks"]), v["hard_failures"] or "none",
        v["soft_findings"] or "none", out))
    return 0 if v["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
