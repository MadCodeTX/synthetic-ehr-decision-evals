#!/bin/bash
# Fine-tune the CLM projection heads on bank v1 DEV only (the same 4,084 rows the Laya fine-tune used).
# Needs the encoder from serve_clm.sh running (embeddings come from it) and pyarrow + transformers in $CLM_PY.
set -u
: "${CLM_REPO:?}" "${CLM_PY:?}" "${CLM_CKPT:?}"
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"; OUT="${OUT:-$PWD/clm-ft-v1dev}"
python3 "$HERE/prep_clm_choice_data.py" --bank "$REPO/data/bank.jsonl" --policy "$REPO/policy/policy.json" --repo "$REPO" --out "$OUT/data"
mkdir -p "$OUT/data/pq/all"
"$CLM_PY" -c "
import json, sys, pyarrow as pa, pyarrow.parquet as pq
for part in ('train', 'test'):
    rows = [json.loads(l) for l in open('$OUT/data/%s.jsonl' % part)]
    pq.write_table(pa.Table.from_pylist(rows), '$OUT/data/pq/all/%s-00000.parquet' % part)"
# Selected on dev validation accuracy among {default infonce 20ep, softce 40ep, infonce 60ep, softce 60ep lr 1e-3}:
cd "$CLM_REPO" && "$CLM_PY" train/finetune.py --task choice --data "$OUT/data/pq" --workflow all \
  --init-ckpt "$CLM_CKPT" --out-dir "$OUT/run" --embed-url http://127.0.0.1:8095/v1/embeddings \
  --served-model-name qwen3-8b --max-len 8192 --gpu 1 --loss softce --epochs 60 --patience 10 --lr 1e-3
