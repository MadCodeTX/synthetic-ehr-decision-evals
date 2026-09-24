#!/usr/bin/env python3
"""Build CLM `--task choice` training rows from bank v1 **dev** only (holdout and v2 never touched).

Each row is the System One wire format the benchmark sends:
  state     = case evidence
  questions = {"choice": {"type": "choice", "instructions": INSTR(policy_text), "criteria": options (presented order)}}
  gold      = {"choice": {"label": expected}}
Rows whose expected label is not an offered option (no_valid_option ambiguity cases) are skipped, the same
4,084-item set the Laya fine-tune used. 5% of dev families are set aside as the trainer's required "test"
split (dev-derived, never holdout); the trainer carves its validation split from "train" itself.

Writes JSON-lines; to_parquet() converts them on the GPU host (pyarrow).
Usage: prep_clm_choice_data.py --bank data/bank.jsonl --policy policy/policy.json --out DIR
"""
import argparse, hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--repo", required=True, help="public repo root (for simple/ehr_eval.prompts.instr)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(a.repo, "simple"))
    from ehr_eval.prompts import instr  # the exact INSTR() the harness sends

    pol = json.load(open(a.policy))
    wfs = pol.get("workflows") or pol
    rows = {"train": [], "test": []}
    skipped = 0
    for line in open(a.bank, encoding="utf-8"):
        c = json.loads(line)
        if c["split"] != "dev":
            continue
        if c["expected"] not in c["options"]:
            skipped += 1
            continue
        fam = c.get("family_id") or c["case_id"]
        part = "test" if int(hashlib.sha256(fam.encode()).hexdigest(), 16) % 20 == 0 else "train"
        rows[part].append({
            "id": c["case_id"], "workflow": c["workflow"],
            "state": json.dumps(c["evidence"], ensure_ascii=False),
            "questions": json.dumps({"choice": {"type": "choice", "instructions": instr(wfs[c["workflow"]]["policy_text"]),
                                                "criteria": c["options"]}}, ensure_ascii=False),
            "gold": json.dumps({"choice": {"label": c["expected"]}}),
        })
    os.makedirs(a.out, exist_ok=True)
    for part, rs in rows.items():
        with open(os.path.join(a.out, part + ".jsonl"), "w", encoding="utf-8") as fh:
            for r in rs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"train": len(rows["train"]), "test": len(rows["test"]), "skipped_no_valid_option": skipped}))


if __name__ == "__main__":
    main()
