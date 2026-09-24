#!/usr/bin/env python3
"""Independent analysis for a paired Qwen-vs-Jev run.

Reads the run directory JSONL streams + frozen fixture bank and produces
metrics.json, CSV summary tables, a failure gallery, and the ambiguity set.
Stdlib only. Never calls a model; never trusts model assertions.
Usage: python3 analyze.py <run_dir> [--bank <bank.jsonl>] [--out metrics]
"""
import argparse, json, math, os, random, statistics, sys
from collections import defaultdict

ABSTAIN = "needs_human_review"
DEV_PHASES = {"pilot", "broad", "counterfactual", "repeat", "load_c1", "load_c2", "load_c4", "load_c8"}
ALL_PHASES = DEV_PHASES | {"seed", "holdout"}


def load_jsonl(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (round(p, 4), round((c - h) / d, 4), round((c + h) / d, 4))


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(math.ceil(q * len(s))) - 1))
    return round(s[i], 4)


def bootstrap_ci(pairs_correct, arm, n=2000, seed=20260923):
    """Paired bootstrap for arm accuracy over the SAME trial set."""
    if not pairs_correct:
        return (None, None)
    rng = random.Random(seed)
    m = len(pairs_correct)
    accs = []
    for _ in range(n):
        hits = sum(pairs_correct[rng.randrange(m)][arm] for _ in range(m))
        accs.append(hits / m)
    accs.sort()
    return (round(accs[int(0.025 * n)], 4), round(accs[int(0.975 * n)], 4))


def mcnemar_exact(b, c):
    """Exact binomial McNemar p-value for discordant counts b (arm1 only) and c (arm2 only)."""
    n = b + c
    if n == 0:
        return None
    k = min(b, c)
    # two-sided
    from math import comb
    p = sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n) * 2
    return min(1.0, round(p, 6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--bank", default=None)
    ap.add_argument("--out", default="metrics")
    args = ap.parse_args()

    rd = args.run_dir
    out = os.path.join(rd, args.out)
    os.makedirs(out, exist_ok=True)

    bank_path = args.bank or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "bank.jsonl")
    bank = {c["case_id"]: c for c in load_jsonl(bank_path)}
    results = load_jsonl(os.path.join(rd, "results.jsonl"))
    pairs = load_jsonl(os.path.join(rd, "pairs.jsonl"))
    attempts = load_jsonl(os.path.join(rd, "attempts.jsonl"))
    manifest = json.load(open(os.path.join(rd, "manifest.json")))
    status = json.load(open(os.path.join(rd, "status.json")))
    gpu = load_jsonl(os.path.join(rd, "gpu.jsonl")) if os.path.exists(os.path.join(rd, "gpu.jsonl")) else []

    res_by = {}
    for r in results:
        res_by[(r["trial_id"], r["arm"])] = r

    # ---------------- core per-trial records ----------------
    trials = []  # one per pair
    for p in pairs:
        j = res_by.get((p["trial_id"], "jev"))
        q = res_by.get((p["trial_id"], "qwen"))
        if not j or not q:
            continue
        case = bank.get(p["case_id"], {})
        trials.append({
            "trial_id": p["trial_id"], "case_id": p["case_id"], "phase": p["phase"],
            "workflow": j["workflow"], "stratum": case.get("stratum", "?"),
            "split": case.get("split", "?"),
            "ambiguity": case.get("ambiguity"),
            "perturbations": case.get("perturbations", {}),
            "expected": p["expected"],
            "jev": j, "qwen": q,
        })

    M = {"run_id": manifest.get("run_id"), "generated_utc": status.get("t_utc"),
         "n_results": len(results), "n_pairs": len(pairs), "n_trials_used": len(trials)}

    # ambiguity set
    amb_ids = sorted({t["case_id"] for t in trials if t["ambiguity"]})
    json.dump({"n_ambiguous_cases": len(amb_ids), "case_ids": amb_ids},
              open(os.path.join(out, "ambiguity_set.json"), "w"), indent=1)

    # ---------------- accuracy / abstention / validity ----------------
    def acc_table(filter_fn):
        tab = defaultdict(lambda: {"n": 0, "amb": 0, "jv": 0, "jv_n": 0, "qv": 0, "qv_n": 0,
                                  "jc": 0, "qc": 0, "ja": 0, "qa": 0, "ja_want": 0, "qa_want": 0,
                                  "j_invalid": 0, "q_invalid": 0, "j_over": 0})
        for t in trials:
            if not filter_fn(t):
                continue
            r = tab[(t["workflow"], t["stratum"] if t["stratum"] != "?" else "unknown")]
            r["n"] += 1
            if t["ambiguity"]:
                r["amb"] += 1
                continue
            j, q = t["jev"], t["qwen"]
            if not j["valid"]:
                r["j_invalid"] += 1
            if not q["valid"]:
                r["q_invalid"] += 1
            r["jv_n"] += 1; r["qv_n"] += 1
            if j["valid"]:
                r["jv"] += 1
            if q["valid"]:
                r["qv"] += 1
            r["jc"] += 1 if j["correct"] else 0
            r["qc"] += 1 if q["correct"] else 0
            want_abstain = t["expected"] == ABSTAIN
            if j["abstained"]:
                r["ja"] += 1
            if q["abstained"]:
                r["qa"] += 1
            if want_abstain:
                r["ja_want"] += 1; r["qa_want"] += 1
            if (not j["correct"]) and j["valid"] and (j.get("confidence") is not None) and j["confidence"] >= 0.9:
                r["j_over"] += 1
        return tab

    def summarize(tab):
        rows = []
        for (wf, st), r in sorted(tab.items()):
            nacc = r["jv_n"]
            row = {"workflow": wf, "stratum": st, "n": r["n"], "n_ambiguous_excluded": r["amb"],
                   "jev_valid": wilson(r["jv"], r["jv_n"]), "qwen_valid": wilson(r["qv"], r["qv_n"]),
                   "jev_acc": wilson(r["jc"], nacc), "qwen_acc": wilson(r["qc"], nacc),
                   "jev_abstain_rate": round(r["ja"] / nacc, 4) if nacc else None,
                   "qwen_abstain_rate": round(r["qa"] / nacc, 4) if nacc else None,
                   "n_expected_abstain": r["ja_want"],
                   "jev_abstained_when_expected": r["ja"] if False else None,
                   "jev_overconfident_errors": r["j_over"]}
            rows.append(row)
        return rows

    dev_rows = summarize(acc_table(lambda t: t["phase"] in DEV_PHASES and t["split"] == "dev"))
    hold_rows = summarize(acc_table(lambda t: t["phase"] == "holdout"))
    seed_rows = summarize(acc_table(lambda t: t["phase"] == "seed"))

    # abstain-when-expected per workflow (dev)
    abst_when_expected = {}
    for wf in ("chart", "inbox", "results"):
        sub = [t for t in trials if t["phase"] in DEV_PHASES and t["workflow"] == wf and not t["ambiguity"] and t["expected"] == ABSTAIN]
        abst_when_expected[wf] = {
            "n": len(sub),
            "jev": wilson(sum(1 for t in sub if t["jev"]["abstained"]), len(sub)),
            "qwen": wilson(sum(1 for t in sub if t["qwen"]["abstained"]), len(sub)),
        }
    # abstain-when-NOT-expected (over-abstention) per workflow (dev)
    over_abstain = {}
    for wf in ("chart", "inbox", "results"):
        sub = [t for t in trials if t["phase"] in DEV_PHASES and t["workflow"] == wf and not t["ambiguity"] and t["expected"] != ABSTAIN]
        over_abstain[wf] = {
            "n": len(sub),
            "jev": wilson(sum(1 for t in sub if t["jev"]["abstained"]), len(sub)),
            "qwen": wilson(sum(1 for t in sub if t["qwen"]["abstained"]), len(sub)),
        }

    M["accuracy_by_workflow_stratum_dev"] = dev_rows
    M["accuracy_by_workflow_stratum_holdout"] = hold_rows
    M["accuracy_seed_historical"] = seed_rows
    M["abstain_when_expected"] = abst_when_expected
    M["over_abstention_when_not_expected"] = over_abstain

    # ---------------- overall per workflow (dev, non-ambiguous) ----------------
    overall = {}
    for wf in ("chart", "inbox", "results"):
        sub = [t for t in trials if t["phase"] in DEV_PHASES and t["workflow"] == wf and not t["ambiguity"]]
        pc = [(t["jev"]["correct"], t["qwen"]["correct"]) for t in sub]
        b = sum(1 for j, q in pc if j and not q)
        c = sum(1 for j, q in pc if q and not j)
        overall[wf] = {
            "n": len(sub),
            "jev_acc": wilson(sum(1 for j, _ in pc if j), len(sub)),
            "qwen_acc": wilson(sum(1 for _, q in pc if q), len(sub)),
            "jev_acc_boot": bootstrap_ci(pc, 0), "qwen_acc_boot": bootstrap_ci(pc, 1),
            "disagree_jev_only_right": b, "disagree_qwen_only_right": c,
            "agree_both_right": sum(1 for j, q in pc if j and q),
            "agree_both_wrong": sum(1 for j, q in pc if not j and not q),
            "mcnemar_exact_p": mcnemar_exact(b, c),
            "jev_valid": wilson(sum(1 for t in sub if t["jev"]["valid"]), len(sub)),
            "qwen_valid": wilson(sum(1 for t in sub if t["qwen"]["valid"]), len(sub)),
        }
    M["overall_dev"] = overall

    # ---------------- perturbation slices (dev, non-ambiguous) ----------------
    pert_keys = ["n_options", "abstain_available", "evidence_style", "field_condition",
                 "distractors", "option_order", "synonyms", "authority_conflict", "injection", "decision_shape"]
    pert = {}
    for key in pert_keys:
        vals = defaultdict(list)
        for t in trials:
            if t["phase"] in DEV_PHASES and not t["ambiguity"]:
                v = t["perturbations"].get(key)
                vals[str(v)].append(t)
        pert[key] = {}
        for v, sub in sorted(vals.items()):
            n = len(sub)
            pert[key][v] = {
                "n": n,
                "jev_acc": wilson(sum(1 for t in sub if t["jev"]["correct"]), n),
                "qwen_acc": wilson(sum(1 for t in sub if t["qwen"]["correct"]), n),
                "jev_abstain_rate": round(sum(1 for t in sub if t["jev"]["abstained"]) / n, 4) if n else None,
                "qwen_abstain_rate": round(sum(1 for t in sub if t["qwen"]["abstained"]) / n, 4) if n else None,
            }
    M["perturbation_slices_dev"] = pert

    # ---------------- injection robustness specifically ----------------
    inj = [t for t in trials if t["phase"] in DEV_PHASES and not t["ambiguity"] and t["perturbations"].get("injection")]
    M["injection_robustness"] = {
        "n": len(inj),
        "jev_acc": wilson(sum(1 for t in inj if t["jev"]["correct"]), len(inj)),
        "qwen_acc": wilson(sum(1 for t in inj if t["qwen"]["correct"]), len(inj)),
        "note": "prompt-injection text embedded as untrusted data; correct = did not follow injected instruction",
    } if inj else {"n": 0}

    # ---------------- counterfactual flips ----------------
    cf = [t for t in trials if t["phase"] == "counterfactual" and not t["ambiguity"]]
    cf_pairs_ok = 0; cf_j = 0; cf_q = 0
    for t in cf:
        cf_j += 1 if t["jev"]["correct"] else 0
        cf_q += 1 if t["qwen"]["correct"] else 0
    M["counterfactuals"] = {"n": len(cf),
                             "jev_acc": wilson(cf_j, len(cf)), "qwen_acc": wilson(cf_q, len(cf)),
                             "note": "one-fact edits of base cases; accuracy on edited case"}

    # ---------------- repeat stability ----------------
    rep = defaultdict(lambda: {"jev": [], "qwen": []})
    for t in trials:
        if t["phase"] == "repeat":
            rep[t["case_id"]]["jev"].append(t["jev"]["choice"])
            rep[t["case_id"]]["qwen"].append(t["qwen"]["choice"])
    def consistency(choices_list):
        stable = sum(1 for ch in choices_list if len(ch) >= 2 and len(set(ch)) == 1)
        return {"n_cases": len(choices_list), "all_reps_identical": wilson(stable, len(choices_list))}
    M["repeat_stability"] = {
        "jev": consistency([v["jev"] for v in rep.values()]),
        "qwen": consistency([v["qwen"] for v in rep.values()]),
        "note": "case-level choice identical across the 3 repeat-panel repetitions",
    }

    # ---------------- latency & throughput ----------------
    lat = {"jev": [], "qwen": []}
    for r in results:
        if r.get("latency_ms_final") is not None:
            lat[r["arm"]].append(r["latency_ms_final"])
    M["latency_ms"] = {a: {"p50": pct(v, .5), "p95": pct(v, .95), "p99": pct(v, .99), "n": len(v)}
                       for a, v in lat.items()}

    load_thru = {}
    for ph in ("load_c1", "load_c2", "load_c4", "load_c8"):
        sub_a = [r for r in results if r["phase"] == ph]
        sub_p = [p for p in pairs if p["phase"] == ph]
        if not sub_p:
            continue
        t0 = min(r["t_start_utc"] for r in sub_a if r.get("t_start_utc"))
        t1 = max(r["t_end_utc"] for r in sub_a if r.get("t_end_utc"))
        try:
            from datetime import datetime
            dt = (datetime.fromisoformat(t1.replace("Z", "+00:00")) - datetime.fromisoformat(t0.replace("Z", "+00:00"))).total_seconds() / 60
        except Exception:
            dt = None
        nvalid = sum(1 for r in sub_a if r["valid"])
        load_thru[ph] = {
            "concurrency": int(ph.replace("load_c", "")),
            "pairs_completed": len(sub_p),
            "valid_decisions": nvalid,
            "valid_decisions_per_min": round(nvalid / dt, 1) if dt else None,
            "jev_valid": sum(1 for r in sub_a if r["arm"] == "jev" and r["valid"]),
            "qwen_valid": sum(1 for r in sub_a if r["arm"] == "qwen" and r["valid"]),
            "jev_acc": wilson(sum(1 for r in sub_a if r["arm"] == "jev" and r["correct"]), sum(1 for r in sub_a if r["arm"] == "jev")),
            "qwen_acc": wilson(sum(1 for r in sub_a if r["arm"] == "qwen" and r["correct"]), sum(1 for r in sub_a if r["arm"] == "qwen")),
        }
    M["load_throughput"] = load_thru

    # ---------------- tokens & qwen rates ----------------
    qtok = [r for r in results if r["arm"] == "qwen"]
    ptok = [r["prompt_tokens"] for r in qtok if r.get("prompt_tokens") is not None]
    ctok = [r["completion_tokens"] for r in qtok if r.get("completion_tokens") is not None]
    rates = [r["e2e_completion_tok_per_s"] for r in qtok if r.get("e2e_completion_tok_per_s") is not None]
    M["qwen_tokens"] = {
        "prompt_mean": round(statistics.mean(ptok), 1) if ptok else None,
        "completion_mean": round(statistics.mean(ctok), 1) if ctok else None,
        "completion_p95": pct(ctok, .95),
        "e2e_completion_tok_per_s_p50": pct(rates, .5),
        "e2e_completion_tok_per_s_p95": pct(rates, .95),
        "note": "e2e rate = completion tokens / client end-to-end latency; NOT decode throughput or TTFT (no streaming)",
    }

    # ---------------- Jev cost ----------------
    jres = [r for r in results if r["arm"] == "jev"]
    jvalid = [r for r in jres if r["valid"]]
    jcorr = [r for r in jres if r["correct"]]
    spent = status.get("spend", {})
    M["jev_cost"] = {
        "reported_usd_total": spent.get("spent_reported_usd"),
        "unknown_cost_charged_usd": spent.get("unknown_cost_charged_usd"),
        "reserved_outstanding_usd": spent.get("reserved_outstanding_usd"),
        "committed_usd": spent.get("committed_usd"),
        "cap_usd": spent.get("max_usd"),
        "usd_per_valid_decision": round(spent.get("spent_reported_usd", 0) / len(jvalid), 6) if jvalid else None,
        "usd_per_correct_decision": round(spent.get("spent_reported_usd", 0) / len(jcorr), 6) if jcorr else None,
        "n_valid": len(jvalid), "n_correct": len(jcorr),
        "note": "reported usage.cost from OpenRouter; unknown attempts charged at reservation, never zero",
    }
    M["qwen_cost"] = {"api_fee_usd": None, "cost_known": False,
                       "note": "OpenAI-compatible endpoint; cost depends on host (openrouter.ai reports usage.cost; self-hosted endpoints are free; hardware/energy cost unmeasured)"}

    # ---------------- validity / failure classification ----------------
    fail = defaultdict(lambda: defaultdict(int))
    for r in results:
        if not r["valid"]:
            fail[r["arm"]][r.get("failure_reason") or "unknown"] += 1
    api_fail = defaultdict(int)
    for a in attempts:
        if a.get("error") or (a.get("http_status") not in (200, None)):
            api_fail[f"{a['arm']}:{a.get('http_status') or 'network'}"] += 1
    M["invalid_by_failure_reason"] = {a: dict(v) for a, v in fail.items()}
    M["api_failures_by_type"] = dict(api_fail)
    M["retries_total"] = status.get("this_segment", {}).get("retries")

    # ---------------- selective accuracy (Jev confidence sweep) ----------------
    jv = [t for t in trials if t["phase"] in DEV_PHASES and not t["ambiguity"] and t["jev"]["valid"]]
    sel = []
    for th in [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]:
        sub = [t for t in jv if (t["jev"].get("confidence") or 0) >= th]
        if not sub:
            continue
        sel.append({"threshold": th, "coverage": round(len(sub) / len(jv), 4),
                    "accuracy": wilson(sum(1 for t in sub if t["jev"]["correct"]), len(sub)),
                    "abstain_rate": round(sum(1 for t in sub if t["jev"]["abstained"]) / len(sub), 4)})
    M["jev_selective_by_confidence"] = sel
    # calibration: mean confidence on correct vs wrong
    jc = [t["jev"]["confidence"] for t in jv if t["jev"]["correct"] and t["jev"].get("confidence") is not None]
    jw = [t["jev"]["confidence"] for t in jv if not t["jev"]["correct"] and t["jev"].get("confidence") is not None]
    M["jev_calibration"] = {
        "mean_confidence_when_correct": round(statistics.mean(jc), 4) if jc else None,
        "mean_confidence_when_wrong": round(statistics.mean(jw), 4) if jw else None,
        "overconfident_errors_ge_0.9": sum(1 for c in jw if c >= 0.9),
        "note": "probabilities are interface outputs, not a calibrated clinical safety guarantee",
    }

    # ---------------- GPU ----------------
    if gpu:
        util = defaultdict(list); power = defaultdict(list)
        for g in gpu:
            raw = g.get("gpus", {})
            # gpu.jsonl has been emitted in two shapes across harness revisions:
            #   list: [{"index":0,"utilization_gpu_pct":100,"power_draw_w":50.02}, ...]
            #   dict: {"0": {"util": 100, "power_w": 50.02}, ...}
            if isinstance(raw, list):
                items = [(str(s.get("index", i)), s) for i, s in enumerate(raw)]
            else:
                items = list(raw.items())
            for gpu_i, s in items:
                u = s.get("util", s.get("utilization_gpu_pct"))
                p = s.get("power_w", s.get("power_draw_w"))
                if u is not None: util[gpu_i].append(u)
                if p is not None: power[gpu_i].append(p)
        M["gpu"] = {i: {"util_mean": round(statistics.mean(v), 1), "util_max": max(v),
                        "power_w_mean": round(statistics.mean(power[i]), 1) if power.get(i) else None,
                        "n_samples": len(v)} for i, v in util.items()}
        M["gpu"]["note"] = "sampled via nvidia-smi over ssh during run; hardware/energy cost not converted to USD (unmeasured)"

    # ---------------- failure gallery ----------------
    gallery = []
    cand = [t for t in trials if t["phase"] in DEV_PHASES and not t["ambiguity"] and
            ((t["jev"]["valid"] and not t["jev"]["correct"]) or (t["qwen"]["valid"] and not t["qwen"]["correct"]))]
    random.Random(20260923).shuffle(cand)
    by_wf = defaultdict(int)
    for t in cand:
        if by_wf[t["workflow"]] >= 8:
            continue
        by_wf[t["workflow"]] += 1
        case = bank.get(t["case_id"], {})
        gallery.append({
            "trial_id": t["trial_id"], "workflow": t["workflow"], "stratum": t["stratum"],
            "kind": "selection_error",
            "evidence": case.get("evidence"), "options": t["jev"].get("options"),
            "expected": t["expected"], "label_basis": case.get("label_basis"),
            "jev_choice": t["jev"]["choice"], "jev_confidence": t["jev"].get("confidence"),
            "jev_probabilities": t["jev"].get("probabilities"),
            "qwen_choice": t["qwen"]["choice"],
            "jev_correct": t["jev"]["correct"], "qwen_correct": t["qwen"]["correct"],
        })
    inv = [t for t in trials if t["phase"] in DEV_PHASES and (not t["jev"]["valid"] or not t["qwen"]["valid"])]
    for t in inv[:10]:
        gallery.append({
            "trial_id": t["trial_id"], "workflow": t["workflow"], "stratum": t["stratum"],
            "kind": "invalid_response", "expected": t["expected"],
            "jev_valid": t["jev"]["valid"], "jev_failure_reason": t["jev"].get("failure_reason"),
            "qwen_valid": t["qwen"]["valid"], "qwen_failure_reason": t["qwen"].get("failure_reason"),
            "jev_raw_answer": t["jev"].get("raw_answer"), "qwen_raw_answer": t["qwen"].get("raw_answer"),
        })
    json.dump(gallery, open(os.path.join(out, "failure_gallery.json"), "w"), indent=1)

    # ---------------- CSVs ----------------
    import csv
    def write_csv(name, rows):
        if not rows:
            return
        keys = sorted({k for r in rows for k in r})
        with open(os.path.join(out, name), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow(r)
    for name, rows in [("accuracy_dev.csv", dev_rows), ("accuracy_holdout.csv", hold_rows),
                       ("accuracy_seed.csv", seed_rows)]:
        write_csv(name, rows)

    json.dump(M, open(os.path.join(out, "metrics.json"), "w"), indent=1)
    print(f"analysis: {len(trials)} paired trials; metrics -> {out}")
    print(f"ambiguous excluded: {len(amb_ids)} cases; invalid: {dict(fail)}")


if __name__ == "__main__":
    main()
