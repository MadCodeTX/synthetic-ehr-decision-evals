#!/usr/bin/env python3
"""Run the EHR decision bank against one LLM arm.

Examples:
  python3 run_eval.py --bank data/bank.jsonl --policy policy/policy.json \
      --plan data/plans/plan_jev.json --phases pilot --arm jev --out-dir runs/py-smoke
  python3 run_eval.py ... --arm openai --base-url http://localhost:8000/v1 --model m

Writes <out-dir>/results.jsonl, pairs.jsonl and summary.json. Stdlib only.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ehr_eval import arms as arms_mod  # noqa: E402
from ehr_eval import bank as bank_mod  # noqa: E402
from ehr_eval import prompts as P  # noqa: E402
from ehr_eval import validate as V  # noqa: E402
from ehr_eval.arms import ArmError  # noqa: E402

PHASES = ["pilot", "broad", "counterfactual", "repeat", "load_c1", "load_c2", "load_c4", "load_c8", "holdout"]
JEV_MODEL = "typesafe/jev-1.13"


def build_arm(a):
    if a.arm == "jev":
        return {"kind": "jev", "url": a.base_url or arms_mod.JEV_URL, "model": a.model or JEV_MODEL,
                "api_key_env": a.api_key_env, "timeout_s": a.timeout_s}
    if a.arm == "openai":
        if not a.base_url or not a.model:
            raise SystemExit("--arm openai requires --base-url and --model")
        return {"kind": "openai", "url": a.base_url.rstrip("/") + "/chat/completions",
                "model": a.model, "api_key_env": a.api_key_env, "timeout_s": a.timeout_s}
    raise SystemExit("--arm must be 'jev' or 'openai'")


def run_trial(arm, arm_id, case, trial, policy, spend, spend_lock, max_usd):
    policy_text = policy["workflows"][case["workflow"]]["policy_text"]
    request = arms_mod.build_request(arm, case["evidence"], case["options"], policy_text)
    out = {"request": request, "cost_usd": 0.0}
    with spend_lock:
        if max_usd is not None and spend["usd"] > max_usd:
            out["blocked"] = "max_usd"
            return out
    ph = P.prompt_hash(arm["kind"], request)
    try:
        res = arms_mod.call_arm(arm, request)
    except ArmError as e:
        out["fatal"] = str(e)
        return out
    out.update(res)
    out["cost_usd"] = res.get("cost_usd") or 0.0
    with spend_lock:
        spend["usd"] += out["cost_usd"]
    keys = list(case["options"].keys())
    body = res.get("body")
    if arm["kind"] == "openai":
        content = (body or {}).get("choices", [{}])[0].get("message", {}).get("content")
        v = V.validate_openai(content, keys)
    else:
        v = V.validate_decisions(body, keys)
    abstain = bank_mod.abstain_key(case, policy)
    out["result"] = {
        "result_id": "%s|%s" % (trial["trial_id"], arm_id), "trial_id": trial["trial_id"],
        "case_id": case["case_id"], "phase": trial["phase"], "rep": trial["rep"], "arm": arm_id,
        "workflow": case["workflow"], "stratum": case["stratum"],
        "input_hash": P.input_hash(case["workflow"], case["evidence"], case["options"], policy_text),
        "prompt_hash": ph, "expected": case["expected"],
        "choice": v["choice"], "valid": v["valid"], "failure_reason": v["failure_reason"],
        "abstained": bool(v["valid"] and abstain and v["choice"] == abstain),
        "correct": bool(v["valid"] and v["choice"] == case["expected"]),
        "latency_ms": res.get("latency_ms"), "attempts": res.get("attempts"),
        "cost_usd": out["cost_usd"], "error": res.get("error"),
    }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--phases", default=",".join(PHASES))
    ap.add_argument("--arm", required=True, choices=["jev", "openai"])
    ap.add_argument("--arm-id", default=None, help="arm id recorded in outputs (default: --arm)")
    ap.add_argument("--base-url", default=None, help="override endpoint (jev) or required base /v1 (openai)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--api-key-env", default=None, help="env var holding the API key (no default; without a key the request is unauthenticated)")
    ap.add_argument("--timeout-s", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="max trials to dispatch")
    ap.add_argument("--max-usd", type=float, default=None, help="stop dispatching new trials above this reported spend")
    ap.add_argument("--out-dir", default="runs/py-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    a = ap.parse_args(argv)

    policy = bank_mod.load_policy(a.policy)
    cases = bank_mod.load_bank(a.bank)
    trials = bank_mod.select_trials(bank_mod.load_plan(a.plan), a.phases.split(","))
    if a.limit is not None:
        trials = trials[:max(0, a.limit)]
    arm = build_arm(a)
    arm_id = a.arm_id or a.arm
    os.makedirs(a.out_dir, exist_ok=True)
    results_f = open(os.path.join(a.out_dir, "results.jsonl"), "w", encoding="utf-8")
    pairs_f = open(os.path.join(a.out_dir, "pairs.jsonl"), "w", encoding="utf-8")
    spend, spend_lock = {"usd": 0.0}, __import__("threading").Lock()
    t0 = time.monotonic()

    def one(t):
        c = cases.get(t["case_id"])
        if c is None:
            return {"fatal": "unknown case %s" % t["case_id"]}
        return run_trial(arm, arm_id, c, t, policy, spend, spend_lock, a.max_usd)

    n_fatal = 0
    with ThreadPoolExecutor(max_workers=max(1, a.concurrency)) as ex:
        for out in ex.map(one, trials):
            r = out.get("result")
            if out.get("fatal"):
                n_fatal += 1
                results_f.write(json.dumps({"trial_id": out.get("result", {}).get("trial_id"),
                                            "arm": arm_id, "fatal": out["fatal"]}) + "\n")
            elif r is not None:
                results_f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
                pairs_f.write(json.dumps({"trial_id": r["trial_id"], "case_id": r["case_id"],
                                          "phase": r["phase"], "arm_order": [arm_id],
                                          "expected": r["expected"], "arm": arm_id,
                                          "choice": r["choice"], "correct": r["correct"],
                                          "input_hash": r["input_hash"]},
                                         ensure_ascii=False, separators=(",", ":")) + "\n")
            if out.get("blocked") == "max_usd" or out.get("fatal"):
                break
    results_f.close()
    pairs_f.close()

    results = [json.loads(l) for l in open(os.path.join(a.out_dir, "results.jsonl"), encoding="utf-8")]
    scored = [r for r in results if r.get("result_id")]
    summary = {"n_trials_planned": len(trials), "n_scored": len(scored), "n_fatal": n_fatal,
               "wall_s": round(time.monotonic() - t0, 3),
               "spend_usd": round(spend["usd"], 6),
               "arm": {"id": arm_id, "kind": arm["kind"], "url": arm["url"], "model": arm["model"]}}
    for scope, keyfn in (("workflow", lambda r: r["workflow"]), ("stratum", lambda r: (r["workflow"], r["stratum"]))):
        acc = {}
        for k in sorted({keyfn(r) for r in scored}, key=str):
            rs = [r for r in scored if keyfn(r) == k]
            acc[str(k)] = {"n": len(rs), "correct": sum(r["correct"] for r in rs),
                           "valid": sum(r["valid"] for r in rs),
                           "abstained": sum(r["abstained"] for r in rs)}
        summary["accuracy_by_" + scope] = acc
    with open(os.path.join(a.out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0 if n_fatal == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
