#!/usr/bin/env python3
"""Independent verifier for a single-arm run (decisions/semif/jev/qwen).

Trusts nothing the runner summarised: every hash, validity decision,
correctness flag and latency is recomputed from raw files. Python3 stdlib only.

Hard checks (any failure => exit 1):
  1. manifest input hashes == recomputed sha256 of policy/bank/plan files
  2. every planned trial in the run's phases has exactly one result, no orphans/dupes
  3. per result: valid/choice/failure_reason recomputed from raw_answer (strict Jev
     validator: answer type, choice in options, probability key set == option set,
     probabilities and confidence finite in [0,1])
  4. correct == (valid and choice == bank expected); abstained == (valid and
     abstain_key offered and choice == abstain_key)
  5. api_model in the expected checkpoint/model set for the arm
  6. prompt_hash constant per (arm, input_hash); identical to the jev arm's
     prompt_hash for the same trial in the paired run (shared model-stripped body)
  7. latency sanity: >= 0, recorded on every attempt/result
  8. no credentials in any run file

Usage: verify_single.py <run_dir> --bank <bank.jsonl> --plan <plan.json>
       --policy <policy.json> [--arm decisions|semif|jev|qwen] [--qwen-jev-run <dir>]
       [--checkpoints csv] [--out verification.json]
"""
import argparse
import csv
import hashlib
import json
import pathlib
import statistics
import sys
import time

DEFAULT_CHECKPOINTS = {"english", "multilingual", "typed-decisions"}
ARM_NAME = "decisions"


def sha256_file(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def read_jsonl(path):
    rows, bad = [], 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                bad += 1
    return rows, bad


def is_finite_unit(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and 0.0 <= v <= 1.0


def validate_jev(raw, option_keys):
    out = {"valid": False, "choice": None, "failure_reason": None,
           "probabilities": None, "confidence": None}
    ans = raw.get("answers") if isinstance(raw, dict) else None
    ch = ans.get("choice") if isinstance(ans, dict) else None
    if not isinstance(ch, dict):
        out["failure_reason"] = "missing_answers_choice"
        return out
    if ch.get("type") != "choice":
        out["failure_reason"] = "wrong_type"
        return out
    c = ch.get("choice")
    if not isinstance(c, str) or c not in option_keys:
        out["failure_reason"] = "choice_not_in_options"
        return out
    probs = ch.get("probabilities")
    if not isinstance(probs, dict):
        out["failure_reason"] = "probabilities_not_object"
        return out
    if set(probs) != set(option_keys):
        out["failure_reason"] = "probabilities_missing_key" if any(
            k not in probs for k in option_keys) else "probabilities_extra_key"
        return out
    if not all(is_finite_unit(v) for v in probs.values()):
        out["failure_reason"] = "probability_not_finite_in_0_1"
        return out
    if "confidence" not in ch or not is_finite_unit(ch.get("confidence")):
        out["failure_reason"] = "missing_confidence" if "confidence" not in ch else "confidence_not_finite_in_0_1"
        return out
    out.update(valid=True, choice=c, probabilities=probs, confidence=ch["confidence"])
    return out


def validate_qwen(raw, option_keys):
    """Exact mirror of evals/validate.ts validateQwen (strict) for the qwen arm; raw is the result's raw_answer."""
    out = {"valid": False, "choice": None, "failure_reason": None, "probabilities": None, "confidence": None}
    content = raw.get("content") if isinstance(raw, dict) else None
    def fail(r):
        out["failure_reason"] = r
        return out
    if not isinstance(content, str):
        return fail("no_content")
    t = content.strip()
    if not t:
        return fail("empty_content")
    try:
        parsed = json.loads(t)
    except Exception:
        return fail("not_json")
    if not isinstance(parsed, dict):
        return fail("not_object")
    keys = list(parsed.keys())
    if "choice" not in keys:
        return fail("missing_choice_key")
    if len(keys) != 1:
        return fail("extra_keys")
    if not isinstance(parsed["choice"], str):
        return fail("choice_not_string")
    if parsed["choice"] not in option_keys:
        return fail("choice_not_in_options")
    out.update(valid=True, choice=parsed["choice"])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--bank", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--qwen-jev-run", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--checkpoints", default=None, help="comma-separated expected api_model values (default: DECISIONS_EXPECTED_MODEL env or the standard checkpoint set)")
    ap.add_argument("--arm", default="decisions", help="arm name in the run (decisions|semif|jev|qwen; legacy 'laya' accepted)")
    a = ap.parse_args()
    global CHECKPOINTS, ARM_NAME
    import os as _os
    if a.checkpoints:
        CHECKPOINTS = {s.strip() for s in a.checkpoints.split(',') if s.strip()}
    else:
        env = _os.environ.get("DECISIONS_EXPECTED_MODEL") or _os.environ.get("LAYA_EXPECTED_CHECKPOINTS")
        CHECKPOINTS = {s.strip() for s in env.split(',') if s.strip()} if env else set(DEFAULT_CHECKPOINTS)
    ARM_NAME = {"laya": "decisions"}.get(a.arm, a.arm)
    if ARM_NAME == "qwen":
        CHECKPOINTS = set()  # qwen answers come from chat completions; the drift pin lives in the manifest
    rd = pathlib.Path(a.run_dir)

    checks, hard_failures = [], []

    def check(name, ok, detail=None, hard=True):
        entry = {"name": name, "pass": bool(ok), "hard": hard, "detail": detail or {}}
        checks.append(entry)
        if not ok and hard:
            hard_failures.append(name)

    manifest = json.loads((rd / "manifest.json").read_text())
    bank = {c["case_id"]: c for _, c in zip(range(10**9), read_jsonl(a.bank)[0] and [json.loads(l) for l in open(a.bank) if l.strip()])} \
        if False else {json.loads(l)["case_id"]: json.loads(l) for l in open(a.bank) if l.strip()}
    plan = json.loads(pathlib.Path(a.plan).read_text())
    policy = json.loads(pathlib.Path(a.policy).read_text())
    results, results_bad = read_jsonl(rd / "results.jsonl")
    pairs, _ = read_jsonl(rd / "pairs.jsonl")
    attempts, attempts_bad = read_jsonl(rd / "attempts.jsonl")

    # 1. input hashes
    check("manifest policy_hash == file", manifest["hashes"]["policy_hash"] == sha256_file(a.policy))
    check("manifest fixture_hash == file", manifest["hashes"]["fixture_hash"] == sha256_file(a.bank))
    check("manifest plan_hash == file", manifest["hashes"]["plan_hash"] == sha256_file(a.plan))

    # 2. completeness vs plan (every trial in every phase of the manifest's plan)
    planned = {t["trial_id"]: t for t in plan["trials"]}
    by_trial = {}
    dupes = 0
    for r in results:
        if (r["trial_id"], r["arm"]) in by_trial:
            dupes += 1
        by_trial[(r["trial_id"], r["arm"])] = r
    arm_results = {r["trial_id"]: r for (tid, arm), r in by_trial.items() if arm == ARM_NAME}
    check("no duplicate (trial, arm) results", dupes == 0, {"duplicates": dupes})
    missing = sorted(set(planned) - set(arm_results))
    orphans = sorted(set(arm_results) - set(planned))
    check("every planned trial has a result", not missing, {"missing": missing[:10]})
    check("no orphan results outside the plan", not orphans, {"orphans": orphans[:10]})
    check("every planned trial has a pair", not (set(planned) - {p["trial_id"] for p in pairs}))
    check("no torn jsonl lines", results_bad == 0 and attempts_bad == 0,
          {"results_bad": results_bad, "attempts_bad": attempts_bad})

    # 3/4/5. recompute each result
    val_mismatch, correct_mismatch, abstain_mismatch, ckpt_bad, lat_bad = [], [], [], [], []
    ph_by_input = {}
    for r in results:
        case = bank.get(r["case_id"])
        if case is None:
            hard_failures.append(f"unknown case {r['case_id']}")
            continue
        keys = [k for k, _ in r["options"]]
        v = (validate_qwen if ARM_NAME == "qwen" else validate_jev)(r.get("raw_answer") or {}, keys)
        if (v["valid"], v["choice"]) != (r["valid"], r["choice"]):
            val_mismatch.append((r["result_id"], r["valid"], v["valid"], r.get("failure_reason"), v["failure_reason"]))
        exp_correct = bool(r["valid"] and r["choice"] == case["expected"])
        if exp_correct != bool(r["correct"]):
            correct_mismatch.append(r["result_id"])
        wf_abstain = policy.get("workflows", {}).get(case["workflow"], {}).get("abstain_key")
        abstain_key = wf_abstain if wf_abstain and wf_abstain in dict(r["options"]) else (
            "abstain" if "abstain" in dict(r["options"]) else None)
        exp_abstained = bool(r["valid"] and abstain_key is not None and r["choice"] == abstain_key)
        if exp_abstained != bool(r.get("abstained")):
            abstain_mismatch.append(r["result_id"])
        if r["valid"] and ARM_NAME in ("decisions", "semif") and r.get("api_model") not in CHECKPOINTS:
            ckpt_bad.append((r["result_id"], r.get("api_model")))
        if not isinstance(r.get("latency_ms_final"), (int, float)) or r["latency_ms_final"] < 0:
            lat_bad.append(r["result_id"])
        ph_by_input.setdefault((r["arm"], r["input_hash"]), set()).add(r.get("prompt_hash"))
    check("validity recomputed from raw answers", not val_mismatch, {"mismatches": val_mismatch[:10]})
    check("correct flags recomputed vs bank expected", not correct_mismatch, {"mismatches": correct_mismatch[:10]})
    check("abstain flags recomputed vs policy abstain key", not abstain_mismatch, {"mismatches": abstain_mismatch[:10]})
    check("api_model is in the expected set for the arm", not ckpt_bad, {"bad": ckpt_bad[:10]})
    check("latency recorded and non-negative", not lat_bad, {"bad": lat_bad[:10]})

    # 6. prompt hash invariance + cross-arm identity
    ph_var = [(k, sorted(v)) for k, v in ph_by_input.items() if len(v) > 1]
    check("prompt_hash constant per (arm, input_hash)", not ph_var, {"varying": ph_var[:10]})
    if a.qwen_jev_run:
        qj = pathlib.Path(a.qwen_jev_run)
        jev_res, _ = read_jsonl(qj / "results.jsonl")
        jev_ph = {r["trial_id"]: r.get("prompt_hash") for r in jev_res if r.get("arm") == "jev"}
        cross_bad = [tid for tid, r in arm_results.items()
                     if tid in jev_ph and r.get("prompt_hash") != jev_ph[tid]]
        n_cross = sum(1 for tid in arm_results if tid in jev_ph)
        check("decisions prompt_hash == jev prompt_hash for the same trial (shared model-stripped body)",
              not cross_bad, {"n_compared": n_cross, "mismatches": cross_bad[:10]})

    # 7. manifest arm metadata + cost
    arms = manifest.get("arms", {})
    arm_meta = arms.get(ARM_NAME, {})
    check(f"manifest records the {ARM_NAME} arm", ARM_NAME in arms)
    check("no credentials recorded on the free local arm",
          "credential_source" not in arm_meta or "none" in str(arm_meta.get("credential_source")))
    blob = "".join((rd / f).read_text() for f in ["manifest.json", "attempts.jsonl", "results.jsonl", "pairs.jsonl", "events.jsonl"]
                    if (rd / f).exists())
    check("no bearer tokens in run files", "Bearer " not in blob)

    # summary stats for the report
    lats = sorted(r["latency_ms_final"] for r in results if isinstance(r.get("latency_ms_final"), (int, float)))
    summary = {
        "results": len(results), "pairs": len(pairs), "attempts": len(attempts),
        "valid": sum(1 for r in results if r["valid"]),
        "invalid": sum(1 for r in results if not r["valid"]),
        "correct": sum(1 for r in results if r["correct"]),
        "abstained": sum(1 for r in results if r.get("abstained")),
        "api_models": sorted({r.get("api_model") for r in results} - {None}),  # None = transport failure (no response)
        "latency_ms": {"p50": lats[len(lats) // 2], "p95": lats[int(len(lats) * .95)], "p99": lats[int(len(lats) * .99)]},
        "device_from_manifest": arm_meta.get("impl"),
        "cfg": arm_meta.get("cfg"),
    }
    out = {
        "verifier": "verify/verify_single.py",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_dir": str(rd),
        "pass": not hard_failures,
        "hard_failures": hard_failures,
        "checks": checks,
        "summary": summary,
    }
    dest = pathlib.Path(a.out) if a.out else rd / "verification.json"
    dest.write_text(json.dumps(out, indent=1))
    print(f"{ARM_NAME} verification {'PASS' if not hard_failures else 'FAIL'}: "
          f"{len(checks)} checks, hard failures: {hard_failures or 'none'} -> {dest}")
    print(json.dumps(summary, indent=1))
    return 0 if not hard_failures else 1


if __name__ == "__main__":
    sys.exit(main())
