#!/usr/bin/env python3
"""Cache frozen-backbone last_hidden_state for every prepared record (head-only fine-tuning).

Exactly the serving path: load_release_model() + encode_record(processor.tokenizer, request) + the text model
forward ClefModel uses, batch size 1 (no padding -- same as systemone()). Hidden states (BF16, all positions
of the unpadded sequence) go to one flat memmap  hidden.bin  [total_tokens, hidden]; index.jsonl has offsets,
input_ids, spans, labels. Also stores the released head's logits computed live (end-to-end baseline) and the
output-embedding matrix (needed for the head's lexical option vectors).

Resumable: records already present in index.jsonl are skipped.
Usage: CUDA_VISIBLE_DEVICES=1 cache_hidden.py <model_dir> <records.jsonl> <cache_dir> [quant_label] [load_kwargs_json]
"""
import json
import os
import sys
import time

import numpy as np
import torch

MODEL_DIR, RECORDS, CACHE = sys.argv[1], sys.argv[2], sys.argv[3]
QUANT = sys.argv[4] if len(sys.argv) > 4 else "bf16"
LOAD_KW = json.loads(sys.argv[5]) if len(sys.argv) > 5 else {}
sys.path.insert(0, MODEL_DIR)
from joint_schema_model import collate_records, encode_record, load_release_model  # noqa: E402

os.makedirs(CACHE, exist_ok=True)
records = [json.loads(l) for l in open(RECORDS, encoding="utf-8")]
t0 = time.time()
if QUANT == "bf16":
    model, processor = load_release_model(MODEL_DIR, device="cuda:0", dtype=torch.bfloat16)
else:
    # quantized backbone (27B): caller passes from_pretrained kwargs (e.g. quantization_config / device_map)
    from transformers import BitsAndBytesConfig
    # MUST mirror serve_clef.py load() exactly, else cached hidden states differ from serving.
    skip = ["lm_head", "visual", "model.visual"]
    kw = dict(LOAD_KW)
    if QUANT == "nf4":
        kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                       bnb_4bit_use_double_quant=True,
                                                       bnb_4bit_compute_dtype=torch.bfloat16,
                                                       llm_int8_skip_modules=skip)
    elif QUANT == "int8":
        kw["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True, llm_int8_skip_modules=skip)
    model, processor = load_release_model(MODEL_DIR, device="cuda:0", dtype=torch.bfloat16, **kw)
print(f"loaded in {time.time() - t0:.0f}s", flush=True)
tok = processor.tokenizer
base = model.language_model
text_model = base.model.language_model
hidden_size = base.config.text_config.hidden_size
lm_w = base.get_output_embeddings().weight
lm_path = os.path.join(CACHE, "lm_head_weight.pt")
if not os.path.exists(lm_path):
    torch.save(lm_w.detach().to("cpu", torch.bfloat16).contiguous(), lm_path)

total = sum(r["n_tokens"] for r in records)
offsets, off = [], 0
for r in records:
    offsets.append(off)
    off += r["n_tokens"]
meta_path = os.path.join(CACHE, "cache_meta.json")
json.dump({"model_dir": MODEL_DIR, "quant": QUANT, "load_kwargs": LOAD_KW, "hidden_size": hidden_size,
           "total_tokens": total, "n_records": len(records), "dtype": "bfloat16",
           "bytes": total * hidden_size * 2, "pad_token_id": tok.pad_token_id}, open(meta_path, "w"), indent=2)
hb_path = os.path.join(CACHE, "hidden.bin")
mode = "r+" if os.path.exists(hb_path) else "w+"
hb = np.memmap(hb_path, dtype=np.uint16, mode=mode, shape=(total, hidden_size))

idx_path = os.path.join(CACHE, "index.jsonl")
done = set()
if os.path.exists(idx_path):
    for l in open(idx_path):
        done.add(json.loads(l)["case_id"])
idx_f = open(idx_path, "a")
t1 = time.time()
n_new = 0
with torch.inference_mode():
    for i, r in enumerate(records):
        if r["case_id"] in done:
            continue
        enc = encode_record(tok, r["request"], max_length=16384, processor=processor)
        assert len(enc.input_ids) == r["n_tokens"], (r["case_id"], len(enc.input_ids), r["n_tokens"])
        batch = collate_records([enc], tok.pad_token_id, torch.device("cuda:0"))
        out = text_model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"],
                         use_cache=False, return_dict=True)
        h = out.last_hidden_state  # [1, L, H] bf16
        logits = model.head(h, batch["input_ids"], batch["attention_mask"], batch["records"], lm_w)[0][0]
        hb[offsets[i]:offsets[i] + r["n_tokens"]] = h[0].contiguous().view(torch.int16).cpu().numpy().view(np.uint16)
        q = enc.questions[0]
        idx_f.write(json.dumps({
            "case_id": r["case_id"], "i": i, "offset": offsets[i], "length": r["n_tokens"],
            "split": r["split"], "workflow": r["workflow"], "stratum": r["stratum"], "label": r["label"],
            "input_ids": list(enc.input_ids),
            "questions": [{"question_id": qq.question_id, "question_type": qq.question_type,
                           "question_span": list(qq.question_span),
                           "option_spans": [list(s) for s in qq.option_spans],
                           "option_ids": list(qq.option_ids)} for qq in enc.questions],
            "release_logits": [round(float(x), 5) for x in logits.float().tolist()],
        }) + "\n")
        n_new += 1
        if n_new % 100 == 0:
            idx_f.flush()
            hb.flush()
            el = time.time() - t1
            print(f"{n_new} new / {len(done) + n_new}/{len(records)}  {el:.0f}s  {el / n_new:.3f}s/rec", flush=True)
hb.flush()
idx_f.close()
print(f"DONE {len(done) + n_new} records, {time.time() - t1:.0f}s caching, total {time.time() - t0:.0f}s", flush=True)
