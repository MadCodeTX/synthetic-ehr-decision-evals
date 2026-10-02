#!/usr/bin/env python3
"""Build Julia fine-tuning records from bank v1 DEV only (the same 4,084 items as the Laya, CLM and Clef fine-tunes).

Source of truth = the exact request bodies the harness sends (harness_bodies_v1dev.jsonl, written by
../clef/build_bodies.ts with evals/clients.ts decisionsBodyText). Each record is the Julia row the service
builds from that body (julia.typed.predict_typed: state, question = instructions, options = criteria
descriptions in presented order), plus the gold option index.

Request-match check: an independent Python rebuild from bank.jsonl + policy.json (simple/ehr_eval/prompts.py)
is encoded with the release's own julia.data.sequence (strict, the serving budgets) and compared token-for-token
with the encoding of the harness body, for EVERY item. Any mismatch fails the run.

Validation split: the Clef procedure, unchanged (~10% of dev items, grouped by family_id so counterfactual
siblings never straddle the split, stratified by (workflow, stratum), seed 0) -> the same 3,652 / 432 split.
Holdout and v2 are never read.

Usage: prep_julia_data.py <julia_release_dir> <repo_dir> <harness_bodies_v1dev.jsonl> <out_dir>
       [--max-length 8192 --head-length 640]
"""
import argparse
import collections
import hashlib
import json
import os
import random
import sys

ap = argparse.ArgumentParser()
ap.add_argument("release")
ap.add_argument("repo")
ap.add_argument("bodies")
ap.add_argument("out")
ap.add_argument("--max-length", type=int, default=8192)
ap.add_argument("--head-length", type=int, default=640)
a = ap.parse_args()

sys.path.insert(0, a.release)
sys.path.insert(0, os.path.join(a.repo, "simple"))
from julia.data import sequence  # noqa: E402
from ehr_eval.prompts import decisions_body_text  # noqa: E402
from transformers import AutoTokenizer  # noqa: E402

VAL_FRAC, SEED = 0.10, 0
tok = AutoTokenizer.from_pretrained(os.path.join(a.release, "tokenizer"), trust_remote_code=False)
policy = json.load(open(os.path.join(a.repo, "policy", "policy.json"), encoding="utf-8"))
bank = {}
for line in open(os.path.join(a.repo, "data", "bank.jsonl"), encoding="utf-8"):
    c = json.loads(line)
    bank[c["case_id"]] = c

rows = [json.loads(l) for l in open(a.bodies, encoding="utf-8")]
assert all(bank[r["case_id"]]["split"] == "dev" for r in rows), "non-dev case in bodies"


def julia_row(body):
    q = body["questions"]["choice"]
    assert q["type"] == "choice" and len(body["questions"]) == 1
    return {"state": body["state"], "question": q["instructions"], "type": "choice",
            "options": list(q["criteria"].values())}, list(q["criteria"])


records, mismatches, text_diffs, lengths = [], [], 0, []
for r in rows:
    body = json.loads(r["body"])
    c = bank[r["case_id"]]
    py_text = decisions_body_text(c["evidence"], c["options"], policy["workflows"][c["workflow"]]["policy_text"],
                                  body["model"])
    if py_text != r["body"]:
        text_diffs += 1
    row_h, keys = julia_row(body)
    row_p, _ = julia_row(json.loads(py_text))
    e_h = sequence(tok, row_h, a.max_length, a.head_length, strict=True)
    e_p = sequence(tok, row_p, a.max_length, a.head_length, strict=True)
    if (e_h["ids"], e_h["markers"]) != (e_p["ids"], e_p["markers"]):
        mismatches.append(r["case_id"])
    assert r["expected"] in keys
    lengths.append(len(e_h["ids"]))
    records.append(dict(row_h, target=keys.index(r["expected"]), case_id=r["case_id"], workflow=r["workflow"],
                        stratum=r["stratum"], family_id=r["family_id"], expected=r["expected"],
                        n_tokens=len(e_h["ids"])))

# ---- grouped, stratified val split (identical procedure to integrations/clef/prep_clef_data.py) ----
fam_items = collections.defaultdict(list)
for i, rec in enumerate(records):
    fam_items[rec["family_id"]].append(i)
key_fams = collections.defaultdict(list)
for fam, idx in fam_items.items():
    first = records[idx[0]]
    key_fams[(first["workflow"], first["stratum"])].append(fam)
rng = random.Random(SEED)
val_fams = set()
for key in sorted(key_fams):
    fams = sorted(key_fams[key])
    rng.shuffle(fams)
    total = sum(len(fam_items[f]) for f in fams)
    got = 0
    for f in fams:
        if got >= VAL_FRAC * total:
            break
        val_fams.add(f)
        got += len(fam_items[f])
for rec in records:
    rec["split"] = "val" if rec["family_id"] in val_fams else "train"

os.makedirs(a.out, exist_ok=True)
with open(os.path.join(a.out, "records.jsonl"), "w", encoding="utf-8") as fh:
    for rec in records:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


ls = sorted(lengths)
meta = {
    "source": "harness_bodies_v1dev.jsonl (evals/clients.ts decisionsBodyText over data/bank.jsonl dev split)",
    "bank_sha256": sha(os.path.join(a.repo, "data", "bank.jsonl")),
    "policy_sha256": sha(os.path.join(a.repo, "policy", "policy.json")),
    "bodies_sha256": sha(a.bodies),
    "encoding": {"max_length": a.max_length, "head_length": a.head_length, "strict": True},
    "n_items": len(records),
    "split_counts": dict(collections.Counter(r["split"] for r in records)),
    "n_val_families": len(val_fams), "n_families": len(fam_items),
    "val_workflow_counts": dict(collections.Counter(r["workflow"] for r in records if r["split"] == "val")),
    "request_match_check": {"compared": len(records), "julia_sequence_mismatches_harness_vs_python_rebuild":
                            len(mismatches), "mismatch_examples": mismatches[:10],
                            "body_text_byte_diffs_vs_python_rebuild": text_diffs},
    "tokens": {"total": sum(lengths), "mean": round(sum(lengths) / len(lengths), 1), "min": ls[0],
               "p50": ls[len(ls) // 2], "p95": ls[int(len(ls) * 0.95)], "max": ls[-1]},
    "n_options_hist": dict(sorted(collections.Counter(len(r["options"]) for r in records).items())),
}
json.dump(meta, open(os.path.join(a.out, "prep_meta.json"), "w"), indent=2)
print(json.dumps(meta, indent=2))
if mismatches:
    sys.exit("REQUEST MISMATCH: harness body encoding differs from python rebuild")
