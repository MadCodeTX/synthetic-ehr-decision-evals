#!/usr/bin/env python3
"""Clef / Clef-flash (Cloudflare) typed-decision service, Jev/SystemOne-compatible.

Clef is a Qwen3.5/3.8 backbone + a joint schema head (joint_head.safetensors) that scores every
allowed option of every question in ONE forward pass (no generation). vLLM cannot run the head, so
this serves it with plain transformers, reusing the release's own joint_schema_model.py
(encode_record / collate_records / JointSchemaHead / ClefModel / systemone) for byte-identical
encoding and answer construction.

HTTP surface (same as the repo's other self-hosted decision services):
  GET  /health        -> {"ok": true, "loaded": [<name>], "cfg": {...}, devices, quant, load time, VRAM}
  POST /v1/systemone  -> joint_schema_model.systemone(...) body: {"model", "answers", "usage"} (+ "service")
                         400 on ValueError/bad JSON, 500 otherwise. Response "model" echoes the request.
  (POST /v1/decisions is accepted as an alias.)

Env:
  CLEF_MODEL_DIR    release dir (config, shards, joint_head*, joint_schema_model.py)   [required]
  CLEF_MODEL_NAME   served model name reported in /health (default: basename of dir)
  CLEF_PORT         default 8096
  CLEF_QUANT        bf16 | int8 | nf4   (bitsandbytes; lm_head + vision tower always kept BF16)
  CLEF_DEVICE_MAP   cuda:0 (single device, default) | auto
  CLEF_MAX_MEMORY   with auto, e.g. "0:22GiB,1:22GiB" (indices are post-CUDA_VISIBLE_DEVICES)
  CLEF_HEAD_PATH    optional fine-tuned joint_head.safetensors (config: sibling joint_head_config.json
                    if present, else the release's)
  CLEF_LORA_DIR     optional PEFT adapter for the backbone (merged into BF16 weights; kept unmerged
                    on quantized backbones)
  CLEF_MAX_LENGTH   encode_record max_length (default 16384, the release default)
  CLEF_CODE_DIR     dir holding joint_schema_model.py (default CLEF_MODEL_DIR)
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL_DIR = Path(os.environ["CLEF_MODEL_DIR"]).expanduser()
MODEL_NAME = os.environ.get("CLEF_MODEL_NAME", MODEL_DIR.name)
PORT = int(os.environ.get("CLEF_PORT", "8096"))
QUANT = os.environ.get("CLEF_QUANT", "bf16").lower()
DEVICE_MAP = os.environ.get("CLEF_DEVICE_MAP", "cuda:0")
MAX_MEMORY = os.environ.get("CLEF_MAX_MEMORY", "")
HEAD_PATH = os.environ.get("CLEF_HEAD_PATH", "")
LORA_DIR = os.environ.get("CLEF_LORA_DIR", "")
MAX_LENGTH = int(os.environ.get("CLEF_MAX_LENGTH", "16384"))
CODE_DIR = Path(os.environ.get("CLEF_CODE_DIR", str(MODEL_DIR))).expanduser()
if QUANT not in ("bf16", "int8", "nf4"):
    raise SystemExit(f"CLEF_QUANT must be bf16, int8 or nf4 (got {QUANT!r})")

sys.path.insert(0, str(CODE_DIR))
import torch  # noqa: E402
from joint_schema_model import ClefModel, JointSchemaHead, systemone  # noqa: E402

# Modules never quantized: lm_head (ClefModel indexes get_output_embeddings().weight for the head's
# lexical option vectors) and the vision tower (small; keep exact).
SKIP_MODULES = ["lm_head", "visual", "model.visual"]


def parse_max_memory(spec: str):
    if not spec:
        return None
    out = {}
    for part in spec.split(","):
        k, v = part.split(":", 1)
        k = k.strip()
        out[int(k) if k.isdigit() else k] = v.strip()
    return out


class ServedClefModel(ClefModel):
    """ClefModel whose forward also works when the backbone is split across devices
    (device_map=auto): hidden states / ids / mask are moved to the head's device, where a usable
    (un-quantized) copy of the output-embedding weight lives."""

    def __init__(self, language_model, head, head_device, out_emb_weight):
        super().__init__(language_model, head)
        self.head_device = head_device
        self.out_emb_weight = out_emb_weight
        self.input_device = language_model.get_input_embeddings().weight.device

    def parameters(self, recurse=True):  # systemone() collates onto next(model.parameters()).device
        yield self.language_model.get_input_embeddings().weight
        yield from super().parameters(recurse)

    def forward(self, batch):
        base_model = (self.language_model.get_base_model()
                      if hasattr(self.language_model, "get_base_model") else self.language_model)
        media = batch.get("media") or {}
        text_model = base_model.model
        if not media and hasattr(text_model, "language_model"):
            text_model = text_model.language_model
        outputs = text_model(input_ids=batch["input_ids"].to(self.input_device),
                             attention_mask=batch["attention_mask"].to(self.input_device),
                             use_cache=False, return_dict=True, **media)
        hd = self.head_device
        return self.head(outputs.last_hidden_state.to(hd), batch["input_ids"].to(hd),
                         batch["attention_mask"].to(hd), batch["records"], self.out_emb_weight)


def load():
    from safetensors.torch import load_file
    from transformers import AutoProcessor, BitsAndBytesConfig, Qwen3_5ForConditionalGeneration

    kwargs = {"dtype": torch.bfloat16}
    if QUANT == "int8":
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_8bit=True, llm_int8_skip_modules=SKIP_MODULES)
    elif QUANT == "nf4":
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16, llm_int8_skip_modules=SKIP_MODULES)
    if DEVICE_MAP == "auto":
        kwargs["device_map"] = "auto"
        mm = parse_max_memory(MAX_MEMORY)
        if mm:
            kwargs["max_memory"] = mm
    else:
        kwargs["device_map"] = {"": DEVICE_MAP}

    backbone = Qwen3_5ForConditionalGeneration.from_pretrained(MODEL_DIR, **kwargs)
    backbone.config.use_cache = False
    if LORA_DIR:
        from peft import PeftModel
        backbone = PeftModel.from_pretrained(backbone, LORA_DIR)
        if QUANT == "bf16":
            backbone = backbone.merge_and_unload()
    backbone.eval()

    base = backbone.get_base_model() if hasattr(backbone, "get_base_model") else backbone
    out_emb = base.get_output_embeddings().weight
    if out_emb.dtype not in (torch.bfloat16, torch.float16, torch.float32):
        raise RuntimeError(f"output embedding weight unusable for the head: dtype={out_emb.dtype} "
                           "(lm_head must stay un-quantized)")
    if out_emb.is_meta:
        # lm_head offloaded to cpu/disk by device_map=auto: read the real weight from the shards and
        # keep it on the first GPU (where accelerate executes offloaded modules).
        from safetensors import safe_open
        idx = json.loads((MODEL_DIR / "model.safetensors.index.json").read_text())["weight_map"]
        key = next(k for k in idx if k.endswith("lm_head.weight"))
        dev = next((d for d in getattr(base, "hf_device_map", {}).values()
                    if isinstance(d, int) or str(d).startswith("cuda")), "cpu")
        dev = f"cuda:{dev}" if isinstance(dev, int) else dev
        with safe_open(str(MODEL_DIR / idx[key]), framework="pt", device="cpu") as f:
            out_emb = f.get_tensor(key).to(device=dev, dtype=torch.bfloat16)
        print(f"[clef-service] lm_head offloaded (meta); loaded {key} onto {dev} for the head", flush=True)
    head_device = out_emb.device

    head_file = Path(HEAD_PATH).expanduser() if HEAD_PATH else MODEL_DIR / "joint_head.safetensors"
    cfg_file = head_file.parent / "joint_head_config.json"
    if not cfg_file.exists():
        cfg_file = MODEL_DIR / "joint_head_config.json"
    head = JointSchemaHead(**json.loads(cfg_file.read_text()))
    head.load_state_dict(load_file(str(head_file)), strict=True)
    head = head.to(device=head_device, dtype=torch.bfloat16).eval()

    processor = AutoProcessor.from_pretrained(MODEL_DIR)
    model = ServedClefModel(backbone, head, head_device, out_emb).eval()
    devmap = getattr(base, "hf_device_map", None) or {"": str(head_device)}
    return model, processor, base, devmap, str(head_file), str(cfg_file)


_t0 = time.time()
model, processor, _base, DEVMAP, HEAD_FILE, HEAD_CFG = load()
LOAD_S = round(time.time() - _t0, 1)
DEVICES = sorted({f"cuda:{v}" if isinstance(v, int) else str(v) for v in DEVMAP.values()})
_lm = _base.get_output_embeddings()
CFG = {"quant": QUANT, "device_map": DEVICE_MAP, "max_memory": MAX_MEMORY or None,
       "devices": DEVICES, "head_device": str(model.head_device), "head_path": HEAD_FILE,
       "lora_dir": LORA_DIR or None, "max_length": MAX_LENGTH, "dtype": "bfloat16",
       "lm_head_type": type(_lm).__name__, "lm_head_dtype": str(_lm.weight.dtype),
       "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES")}
IMPL = "clef joint_schema_model.systemone via transformers (serve_clef.py)"


def vram():
    if not torch.cuda.is_available():
        return {}
    return {f"cuda:{i}": {"alloc_gib": round(torch.cuda.memory_allocated(i) / 2**30, 2),
                          "peak_alloc_gib": round(torch.cuda.max_memory_allocated(i) / 2**30, 2),
                          "peak_reserved_gib": round(torch.cuda.max_memory_reserved(i) / 2**30, 2)}
            for i in range(torch.cuda.device_count())}


print(f"[clef-service] loaded {MODEL_DIR} as '{MODEL_NAME}' in {LOAD_S}s; cfg={json.dumps(CFG)}; "
      f"vram={json.dumps(vram())}", flush=True)

_lock = threading.Lock()
_stats = {"requests": 0, "errors": 0, "predict_ms_sum": 0.0}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "model_name": MODEL_NAME, "loaded": [MODEL_NAME],
                             "device": ",".join(DEVICES), "devices": DEVICES, "quantization": QUANT,
                             "load_s": LOAD_S, "cfg": CFG, "repo": str(MODEL_DIR), "impl": IMPL,
                             "vram": vram(), "stats": _stats, "uptime_s": round(time.time() - _t0)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/v1/systemone", "/v1/decisions"):
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(n))
            except Exception as e:
                raise ValueError(f"invalid JSON body: {e}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            with _lock:
                started = time.perf_counter()
                out = systemone(model, processor, body, max_length=MAX_LENGTH)
                dt_ms = (time.perf_counter() - started) * 1000
            _stats["requests"] += 1
            _stats["predict_ms_sum"] += dt_ms
            out["service"] = {"impl": IMPL, "served_model": MODEL_NAME, "quant": QUANT,
                              "devices": DEVICES, "predict_ms": round(dt_ms, 1),
                              "input_tokens": out["usage"]["input_tokens"]}
            brief = {k: (a.get("choice") if a.get("type") == "choice" else a.get("score", a.get("noul")))
                     for k, a in out["answers"].items()}
            print(f"[req] {dt_ms:.0f}ms in={out['usage']['input_tokens']}tok {brief}", flush=True)
            self._json(200, out)
        except ValueError as e:
            _stats["errors"] += 1
            print("[400]", repr(e)[:300], flush=True)
            self._json(400, {"error": {"message": str(e)[:500]}})
        except Exception as e:
            _stats["errors"] += 1
            print("[err]", repr(e)[:300], flush=True)
            self._json(500, {"error": {"message": str(e)[:300]}})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
