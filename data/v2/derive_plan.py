#!/usr/bin/env python3
"""Derive a single-arm plan from a paired jev-qwen shard plan: same trial ids and order.
Usage: python3 data/v2/derive_plan.py data/v2/plans/plan_v2_s00_jev-qwen.json --arm decisions --out runs/plan_s00_decisions.json
Arms: jev | qwen | decisions | semif  (the harness also accepts the legacy name laya for decisions)."""
import argparse, json
ap = argparse.ArgumentParser()
ap.add_argument("plan"); ap.add_argument("--arm", required=True, choices=["jev", "qwen", "decisions", "laya", "semif"]); ap.add_argument("--out", required=True)
a = ap.parse_args()
p = json.load(open(a.plan))
for t in p["trials"]:
    t["arm_order"] = [a.arm]
p["derived_from"] = a.plan.replace("\\", "/").split("/")[-1]
p["plan_version"] = str(p.get("plan_version", "v2")).replace("jev-qwen", a.arm)
json.dump(p, open(a.out, "w"), separators=(",", ":"))
print("wrote", a.out, len(p["trials"]), "trials")
