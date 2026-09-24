#!/usr/bin/env python3
"""make_plans.py -- sharded plan generator for the v2 bank (PLAN-BANK-V2, Appendix E).

Usage:
    python3 tools/make_plans.py <bank.jsonl> --order-seed <int> \
        --arms <jev-qwen|laya|semif> --out out/plans

Reads a bank file (v1 or v2 schema), assigns every case to exactly one shard and
one main phase (pilot / broad / counterfactual / holdout), plus the additional
load_c1..c8 and repeat trials, and writes one plan file per shard:

    plan_v2_sNN_<arms>.json

plus a MANIFEST.json. Stdlib only; deterministic (no global `random`).

Phases (only names the harness runner knows):
    pilot, broad, counterfactual, repeat, load_c1, load_c2, load_c4, load_c8, holdout
There is no `seed` phase: seed-split cases (v1 banks) are skipped.
"""

import argparse
import hashlib
import json
import os
import random
import sys

PHASES = ["pilot", "broad", "counterfactual", "repeat",
          "load_c1", "load_c2", "load_c4", "load_c8", "holdout"]
WORKFLOWS = ["chart", "inbox", "results"]

# Holdout-only strata (Appendix A.3): all of their cases go into shard 00 holdout.
HOLDOUT_ONLY_STRATA = ("two_edit_name", "same_destination_multi_request",
                       "rule_order_collisions")

MAX_TRIALS = 9500          # hard cap per shard (harness limit is 10,000)
BROAD_TARGET = 4300        # shard 00 broad phase target
CF_TARGET = 1000           # shard 00 counterfactual sibling-trial target
HOLDOUT_TARGET = 3000      # shard 00 holdout target
PILOT_PER_WF = 30          # 30 per workflow -> 90
LOAD_PER_WF = 50           # 50 per workflow per load phase -> 150 per phase
LOAD_REPS = {"load_c1": 10, "load_c2": 11, "load_c4": 12, "load_c8": 13}

NOTES = ("Shard 00 is the core shard (runs alone for quick comparisons): pilot 90, "
         "broad ~4300 (stratified dev-base sample + all repeat-panel bases), "
         "counterfactual ~1000 (whole families whose base is in shard 00), repeat 360 "
         "(repeat-panel reps 1-3; rep 0 is their broad trial), load_c1/c2/c4/c8 150 each "
         "(reps 10-13), holdout ~3000 (all holdout-only-stratum cases + stratified fill). "
         "Shards 01..NN hold every remaining case exactly once: dev bases -> broad, "
         "siblings -> counterfactual, holdout -> holdout; families stay in one shard. "
         "No seed phase: v2 has no seed cases. "
         "Counterfactual bases are not re-run: each dev base case's comparison trial is "
         "its broad (or pilot) trial. Load phases use reps 10..13 (c1,c2,c4,c8).")


def rng_for(order_seed, *tags):
    """Explicit, tagged RNG: determinism without the global `random` module state."""
    return random.Random("make_plans|%d|%s" % (order_seed, "|".join(tags)))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def pick_stratified(cases, k, rng):
    """v1-style round-robin across strata: shuffle within each stratum, then take
    cases round-robin in a shuffled stratum order until k are drawn (or pools empty)."""
    by = {}
    for c in cases:
        by.setdefault(c["stratum"], []).append(c)
    for s in by:
        rng.shuffle(by[s])
    order = sorted(by)
    rng.shuffle(order)
    out = []
    while len(out) < k and any(by[s] for s in order):
        for s in order:
            if len(out) >= k:
                break
            if by[s]:
                out.append(by[s].pop())
    return out


def pick_proportional(cases, k, rng):
    """Deterministic stratified sample proportional to stratum size (largest-remainder
    allocation within strata after per-stratum shuffle)."""
    k = min(k, len(cases))
    if k <= 0:
        return []
    by = {}
    for c in cases:
        by.setdefault(c["stratum"], []).append(c)
    strata = sorted(by)
    for s in strata:
        rng.shuffle(by[s])
    total = len(cases)
    quota = {}
    rema = []
    assigned = 0
    for s in strata:
        exact = len(by[s]) * k / float(total)
        q = int(exact)                      # floor
        q = min(q, len(by[s]))
        quota[s] = q
        assigned += q
        rema.append((-(exact - int(exact)), s))   # sort key: largest remainder first
    rema.sort()
    i = 0
    while assigned < k:
        s = rema[i % len(rema)][1]
        if quota[s] < len(by[s]):
            quota[s] += 1
            assigned += 1
        i += 1
        if i > 10 * len(strata) + 10:       # cannot happen; guard against loops
            break
    out = []
    for s in strata:
        out.extend(by[s][:quota[s]])
    rng.shuffle(out)
    return out


def build_shard00(cases, order_seed):
    """Core shard. Returns (phases dict {phase: [(case_id, rep)]}, assigned_ids set)."""
    dev = [c for c in cases if c["split"] == "dev"]
    holdout = [c for c in cases if c["split"] == "holdout"]
    bases = [c for c in dev if c["counterfactual_of"] is None]
    siblings = [c for c in dev if c["counterfactual_of"] is not None]
    fam_has_sib = set(c["family_id"] for c in siblings)
    repeat_ids = set(c["case_id"] for c in cases if c.get("repeat_panel"))

    # --- pilot: 30 per workflow, dev bases with no dev siblings, non-ambiguous,
    #     non-repeat-panel; stratified round-robin, deterministic shuffle.
    pilot = []
    for wf in WORKFLOWS:
        pool = [c for c in bases
                if c["workflow"] == wf
                and c["ambiguity"] is None
                and c["family_id"] not in fam_has_sib
                and c["case_id"] not in repeat_ids]
        pilot.extend(pick_stratified(pool, PILOT_PER_WF, rng_for(order_seed, "pilot", wf)))
    pilot_ids = set(c["case_id"] for c in pilot)

    # --- load phases: 50 per workflow per phase, dev non-ambiguous, stratified
    #     round-robin; reps 10..13. These are additional trials (the case's main
    #     broad trial may live in any shard).
    load = {}
    for p in sorted(LOAD_REPS):
        load[p] = []
        for wf in WORKFLOWS:
            pool = [c for c in dev if c["workflow"] == wf and c["ambiguity"] is None]
            load[p].extend(pick_stratified(pool, LOAD_PER_WF, rng_for(order_seed, "load", p, wf)))

    # --- broad: all repeat-panel bases + proportional stratified sample of the
    #     remaining dev bases (pilot picks excluded). Target ~4300 broad trials.
    repeat_cases = [c for c in cases if c["case_id"] in repeat_ids]
    broad = list(repeat_cases)
    pool = [c for c in bases
            if c["case_id"] not in pilot_ids and c["case_id"] not in repeat_ids]
    broad.extend(pick_proportional(pool, max(0, BROAD_TARGET - len(repeat_cases)),
                                   rng_for(order_seed, "broad")))
    broad_ids = set(c["case_id"] for c in broad)

    # --- counterfactual: whole families; a sibling is included only if its base is
    #     in shard 00 broad (or pilot). Sample families (deterministic shuffle) until
    #     ~1000 sibling trials.
    base_in_s00 = broad_ids | pilot_ids
    fam_sibs = {}
    for c in siblings:
        fam_sibs.setdefault(c["family_id"], []).append(c)
    elig = [f for f in sorted(fam_sibs)
            if any(c["counterfactual_of"] in base_in_s00 for c in fam_sibs[f])]
    rng_for(order_seed, "cf").shuffle(elig)
    cf = []
    for f in elig:
        if len(cf) >= CF_TARGET:
            break
        cf.extend(fam_sibs[f])
    cf_ids = set(c["case_id"] for c in cf)

    # --- holdout: ALL cases of the 3 holdout-only strata, then fill the remaining
    #     quota with a stratified round-robin sample of the other holdout strata.
    holdout00 = [c for c in holdout if c["stratum"] in HOLDOUT_ONLY_STRATA]
    rest = [c for c in holdout if c["stratum"] not in HOLDOUT_ONLY_STRATA]
    k = min(HOLDOUT_TARGET, len(holdout)) - len(holdout00)
    holdout00.extend(pick_stratified(rest, max(0, k), rng_for(order_seed, "holdout")))
    if len(holdout00) > HOLDOUT_TARGET:
        # safety valve for pathological banks: keep a stratified sample of the target
        holdout00 = pick_stratified(holdout00, HOLDOUT_TARGET, rng_for(order_seed, "holdout-cap"))

    phases = {p: [] for p in PHASES}
    for c in pilot:
        phases["pilot"].append((c["case_id"], 0))
    for c in broad:
        phases["broad"].append((c["case_id"], 0))          # repeat-panel rep 0 = broad trial
    for c in cf:
        phases["counterfactual"].append((c["case_id"], 0))
    for c in repeat_cases:
        for r in (1, 2, 3):
            phases["repeat"].append((c["case_id"], r))
    for p in sorted(LOAD_REPS):
        for c in load[p]:
            phases[p].append((c["case_id"], LOAD_REPS[p]))
    for c in holdout00:
        phases["holdout"].append((c["case_id"], 0))

    assigned = pilot_ids | broad_ids | cf_ids | set(c["case_id"] for c in holdout00)
    return phases, assigned


def build_remaining_shards(cases, assigned, order_seed):
    """Shards 01..NN: every remaining case exactly once. Remaining dev bases ->
    broad, remaining siblings -> counterfactual (whole families packed together),
    remaining holdout -> holdout. Greedy pack to <= MAX_TRIALS per shard."""
    units = []
    fams = {}
    for c in cases:
        if c["split"] not in ("dev", "holdout") or c["case_id"] in assigned:
            continue
        if c["split"] == "holdout":
            units.append([(c["case_id"], "holdout", 0)])
        else:
            fams.setdefault(c["family_id"], []).append(c)
    for f in sorted(fams):
        trials = []
        for c in fams[f]:
            phase = "counterfactual" if c["counterfactual_of"] is not None else "broad"
            trials.append((c["case_id"], phase, 0))
        units.append(trials)
    rng_for(order_seed, "pack").shuffle(units)

    shards = []
    cur, cur_n = [], 0
    for u in units:
        if cur and cur_n + len(u) > MAX_TRIALS:
            shards.append(cur)
            cur, cur_n = [], 0
        cur.extend(u)
        cur_n += len(u)
    if cur:
        shards.append(cur)

    out = []
    for sh in shards:
        phases = {p: [] for p in PHASES}
        for cid, phase, rep in sh:
            phases[phase].append((cid, rep))
        out.append(phases)
    return out


def write_plan(phases, shard_index, arms, order_seed, bank_sha, path):
    trials = []
    rng = random.Random(order_seed + shard_index)   # per-shard deterministic shuffle
    for p in PHASES:
        lst = sorted(phases[p])
        rng.shuffle(lst)
        for cid, rep in lst:
            trials.append({"trial_id": "%s#%s#r%d" % (cid, p, rep),
                           "case_id": cid, "phase": p, "rep": rep, "arm_order": []})
    arm_rng = random.Random(order_seed)             # per-trial arm draw, trial order
    for t in trials:
        if arms == "jev-qwen":
            t["arm_order"] = ["jev", "qwen"] if arm_rng.random() < 0.5 else ["qwen", "jev"]
        else:
            t["arm_order"] = [arms]
    ids = [t["trial_id"] for t in trials]
    assert len(ids) == len(set(ids)), "duplicate trial_id in shard %02d" % shard_index
    plan = {"plan_version": "v2-s%02d-%s" % (shard_index, arms),
            "order_seed": order_seed,
            "phase_counts": {p: len(phases[p]) for p in PHASES},
            "total_trials": len(trials),
            "notes": NOTES,
            "derived_from": bank_sha,
            "trials": trials}
    with open(path, "w") as f:
        json.dump(plan, f, indent=1)
        f.write("\n")
    return plan


def main():
    ap = argparse.ArgumentParser(description="Appendix E plan sharding for bank v2")
    ap.add_argument("bank", help="path to bank .jsonl (v1 or v2 schema)")
    ap.add_argument("--order-seed", type=int, required=True)
    ap.add_argument("--arms", choices=["jev-qwen", "laya", "semif"], default="jev-qwen")
    ap.add_argument("--out", default="out/plans")
    args = ap.parse_args()

    with open(args.bank, "rb") as f:
        data = f.read()
    bank_sha = sha256_bytes(data)
    cases = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    n_seed = sum(1 for c in cases if c["split"] == "seed")
    cases = [c for c in cases if c["split"] != "seed"]   # seed cases are never planned

    os.makedirs(args.out, exist_ok=True)
    phases00, assigned = build_shard00(cases, args.order_seed)
    shard_phases = [phases00] + build_remaining_shards(cases, assigned, args.order_seed)

    files = []
    for i, ph in enumerate(shard_phases):
        fname = "plan_v2_s%02d_%s.json" % (i, args.arms)
        path = os.path.join(args.out, fname)
        plan = write_plan(ph, i, args.arms, args.order_seed, bank_sha, path)
        with open(path, "rb") as f:
            fsha = sha256_bytes(f.read())
        files.append({"file": fname, "sha256": fsha, "trials": plan["total_trials"],
                      "phase_counts": plan["phase_counts"]})
        print("%s  %5d trials  %s" % (fname, plan["total_trials"],
                                      " ".join("%s=%d" % (p, plan["phase_counts"][p])
                                               for p in PHASES if plan["phase_counts"][p])))

    manifest = {"files": files, "order_seed": args.order_seed, "arms": args.arms,
                "derived_from": bank_sha}
    mpath = os.path.join(args.out, "MANIFEST.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    print("MANIFEST.json written (%d shards, %d seed cases skipped, bank sha %s)"
          % (len(files), n_seed, bank_sha[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
