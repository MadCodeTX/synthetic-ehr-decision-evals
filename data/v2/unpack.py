#!/usr/bin/env python3
"""Unpack data/v2/bank_v2.jsonl.gz -> data/v2/bank_v2.jsonl and verify its sha256 (stdlib only).
(The raw bank is ~134 MB, above GitHub's per-file limit, so it is stored gzipped.)"""
import gzip, hashlib, os, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED = open(os.path.join(HERE, "bank_v2.jsonl.sha256")).read().split()[0]
src, dst = os.path.join(HERE, "bank_v2.jsonl.gz"), os.path.join(HERE, "bank_v2.jsonl")
with gzip.open(src, "rb") as fi, open(dst, "wb") as fo:
    shutil.copyfileobj(fi, fo)
h = hashlib.sha256(open(dst, "rb").read()).hexdigest()
if h != EXPECTED:
    raise SystemExit("sha256 mismatch: %s != %s" % (h, EXPECTED))
print("ok", dst, h)
