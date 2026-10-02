#!/usr/bin/env python3
"""Build Clef fine-tuning records from bank v1 DEV only (the 4,084 Laya-FT / CLM-FT items).

Source of truth = the exact request bodies the harness sends (harness_bodies_v1dev.jsonl, produced by
build_bodies.ts with evals/clients.ts decisionsBodyText). Each training record is json.loads(body) -- i.e.
exactly what the serving endpoint parses -- plus the gold label.

Request-match check: an independent Python rebuild from bank.jsonl + policy.json (prompts.instr, presented
option order) is encoded with joint_schema_model.encode_record, and its input_ids / spans / option ids are
compared with the encoding of the harness body for EVERY item. Any mismatch is reported (and fails the run).

Validation split: ~10% of dev items, grouped by family_id (counterfactual siblings never straddle the split),
stratified by (workflow, stratum). Holdout and v2 are never read.

Usage: prep_clef_data.py <model_dir> <data_dir> <out_dir>
"""
import collections
import hashlib
import json
import os
import random
import sys

MODEL_DIR, DATA_DIR, OUT_DIR = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, MODEL_DIR)
sys.path.insert(0, DATA_DIR)
from joint_schema_model import encode_record  # noqa: E402
from prompts import instr  # noqa: E402  (byte-identical port of harness INSTR)
from transformers import AutoTokenizer  # noqa: E402

VAL_FRAC = 0.10
SEED = 0
MAX_LENGTH = 16384  # systemone() default

tok = AutoTokenizer.from_pretrained(MODEL_DIR)
policy = json.load(open(os.path.join(DATA_DIR, "policy.json")))
bank = {}
for line in open(os.path.join(DATA_DIR, "bank.jsonl"), encoding="utf-8"):
    c = json.loads(line)
    bank[c["case_id"]] = c

rows = [json.loads(l) for l in open(os.path.join(DATA_DIR, "harness_bodies_v1dev.jsonl"), encoding="utf-8")]
assert all(bank[r["case_id"]]["split"] == "dev" for r in rows), "non-dev case in bodies"


def enc_sig(e):
    return (e.input_ids, tuple((q.question_id, q.question_type, q.question_span, q.option_spans, q.option_ids)
                               for q in e.questions))


records, mismatches, body_text_diffs = [], [], 0
lengths = []
for r in rows:
    body = json.loads(r["body"])
    c = bank[r["case_id"]]
    # independent python path (what a naive prep would do)
    py_req = {"model": body["model"], "state": c["evidence"],
              "questions": {"choice": {"type": "choice",
                                       "instructions": instr(policy["workflows"][c["workflow"]]["policy_text"]),
                                       "criteria": dict(c["options"])}}}
    if json.dumps(py_req, ensure_ascii=False, separators=(",", ":")) != r["body"]:
        body_text_diffs += 1
    e_h = encode_record(tok, body, max_length=MAX_LENGTH)
    e_p = encode_record(tok, py_req, max_length=MAX_LENGTH)
    if enc_sig(e_h) != enc_sig(e_p):
        mismatches.append(r["case_id"])
    q = e_h.questions[0]
    assert q.question_id == "choice" and len(e_h.questions) == 1
    assert r["expected"] in q.option_ids
    full_state_len = len(tok(json.dumps(body["state"], ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                             add_special_tokens=False).input_ids)
    lengths.append(len(e_h.input_ids))
    records.append({
        "case_id": r["case_id"], "workflow": r["workflow"], "stratum": r["stratum"], "family_id": r["family_id"],
        "expected": r["expected"], "label": q.option_ids.index(r["expected"]), "n_options": len(q.option_ids),
        "n_tokens": len(e_h.input_ids), "state_truncated": full_state_len > MAX_LENGTH,
        "request": body,
    })

# ---- grouped, stratified val split ----
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

os.makedirs(OUT_DIR, exist_ok=True)
with open(os.path.join(OUT_DIR, "records.jsonl"), "w", encoding="utf-8") as fh:
    for rec in records:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


ls = sorted(lengths)
split_counts = collections.Counter(r["split"] for r in records)
meta = {
    "source": "harness_bodies_v1dev.jsonl (evals/clients.ts decisionsBodyText over public repo data/bank.jsonl dev split)",
    "bank_sha256": sha(os.path.join(DATA_DIR, "bank.jsonl")),
    "policy_sha256": sha(os.path.join(DATA_DIR, "policy.json")),
    "bodies_sha256": sha(os.path.join(DATA_DIR, "harness_bodies_v1dev.jsonl")),
    "n_items": len(records),
    "split_counts": dict(split_counts),
    "n_val_families": len(val_fams), "n_families": len(fam_items),
    "val_workflow_counts": dict(collections.Counter(r["workflow"] for r in records if r["split"] == "val")),
    "train_workflow_counts": dict(collections.Counter(r["workflow"] for r in records if r["split"] == "train")),
    "val_stratum_counts": dict(sorted(collections.Counter(f'{r["workflow"]}/{r["stratum"]}' for r in records if r["split"] == "val").items())),
    "request_match_check": {
        "compared": len(records),
        "encode_record_mismatches_harness_vs_python_rebuild": len(mismatches),
        "mismatch_examples": mismatches[:10],
        "body_text_byte_diffs_vs_python_json_dumps": body_text_diffs,
    },
    "tokens": {"total": sum(lengths), "mean": sum(lengths) / len(lengths), "min": ls[0],
               "p50": ls[len(ls) // 2], "p95": ls[int(len(ls) * 0.95)], "max": ls[-1]},
    "n_state_truncated": sum(r["state_truncated"] for r in records),
    "n_options_hist": dict(sorted(collections.Counter(r["n_options"] for r in records).items())),
}
json.dump(meta, open(os.path.join(OUT_DIR, "prep_meta.json"), "w"), indent=2)
print(json.dumps(meta, indent=2))
if mismatches:
    sys.exit("REQUEST MISMATCH: harness body encoding differs from python rebuild")
