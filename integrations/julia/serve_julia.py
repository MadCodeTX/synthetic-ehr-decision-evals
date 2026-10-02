#!/usr/bin/env python3
"""Julia-1 (SupersonicLabs) typed-decision service, Jev/SystemOne-compatible.

Julia-1 is a 144M-parameter mmBERT-small (ModernBERT) encoder plus a small decision head that scores every
offered option in one forward pass (no generation). Its own runtime already answers the named-question
interface `predict(state=..., questions={qid: {type, instructions, criteria}})`, i.e. the body of a Jev
Decisions request minus `model`. This service is a thin HTTP shell around the release's own `julia`
package (`julia.load_model` -> FastEngine, PyTorch backend only; the optional native Bend/C router is never
built or loaded). It adds only what the Jev wire format needs on top of Julia's answer:
  - `confidence` (Julia calls it `max_probability`; for `noul`, max(p, 1 - p)),
  - the response `model` (the served model name) and `usage.input_tokens`.

HTTP surface (same as the repo's other self-hosted decision services):
  GET  /health        -> {"ok": true, "loaded": [<name>], "cfg": {...}, ...}
  POST /v1/systemone  -> {"model", "answers", "usage", "service"}; 400 on invalid requests (including
                         strict-encoding overflow), 500 otherwise.
  (POST /v1/decisions is accepted as an alias.)

Env:
  JULIA_MODEL_DIR    checkpoint dir: model.safetensors, julia_config.json, encoder/, tokenizer/  [required]
                     (the release, or a fine-tuned checkpoint written by train_julia.py)
  JULIA_CODE_DIR     dir holding the release's `julia/` package (default JULIA_MODEL_DIR)
  JULIA_MODEL_NAME   served model name (default: basename of JULIA_MODEL_DIR)
  JULIA_DEVICE       cuda | cuda:N | cpu (default cuda if available, else cpu)
  JULIA_MAX_LENGTH   combined token limit (default 8192 = the release's inference-policy.json)
  JULIA_HEAD_LENGTH  question+options token budget (default 640; the release default is 512, which is too
                     small for ~7% of this benchmark's requests: see README)
  JULIA_STRICT       1 (default; release inference policy): reject any request that would be truncated
  JULIA_PORT         TCP port (default 8098), bound to JULIA_HOST (default 127.0.0.1)
  JULIA_UDS          listen on this unix socket instead of TCP (for `docker run --network none`)
"""
import json
import os
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IMPL = "julia.load_model FastEngine (torch backend) via serve_julia.py"


def question_rows(state, questions):
    """The rows julia.typed.predict_typed builds, for the token audit in `usage` (same validation)."""
    rows = []
    for q in questions.values():
        kind, criteria = q.get("type"), q.get("criteria")
        if kind == "choice":
            labels = list(criteria.values())
        elif kind == "score":
            labels = list(criteria)
        else:
            labels = ["false", "true"] if criteria is None else [criteria["false"], criteria["true"]]
        rows.append(dict(state=state, question=q.get("instructions"), type=kind, options=labels))
    return rows


def answer(engine, body, model_name, strict=True):
    """Map one Jev/SystemOne request body to a Jev-shaped response using Julia's own predict()."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    if "state" not in body:
        raise ValueError("missing state")
    questions = body.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions must be a nonempty object")
    out = engine.predict(state=body["state"], questions=questions)
    answers = {}
    for qid, a in out["answers"].items():
        a = dict(a)
        if a["type"] == "noul":
            a["confidence"] = max(a["noul"], 1.0 - a["noul"])
        else:
            a["confidence"] = a["max_probability"]
        answers[qid] = a
    usage = {}
    if strict and hasattr(engine, "encoding_info"):
        info = engine.encoding_info(question_rows(body["state"], questions))
        usage = {"input_tokens": sum(i["tokens"] for i in info)}
    return {"model": model_name, "answers": answers, "usage": usage}


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def make_handler(engine, model_name, strict, health):
    lock = threading.Lock()
    stats = {"requests": 0, "errors": 0, "predict_ms_sum": 0.0}

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
                self._json(200, dict(health(), stats=stats))
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
                with lock:
                    started = time.perf_counter()
                    out = answer(engine, body, model_name, strict)
                    dt_ms = (time.perf_counter() - started) * 1000
                stats["requests"] += 1
                stats["predict_ms_sum"] += dt_ms
                out["service"] = {"impl": IMPL, "served_model": model_name, "predict_ms": round(dt_ms, 2)}
                self._json(200, out)
            except ValueError as e:
                stats["errors"] += 1
                print("[400]", repr(e)[:300], flush=True)
                self._json(400, {"error": {"message": str(e)[:500]}})
            except Exception as e:
                stats["errors"] += 1
                print("[err]", repr(e)[:300], flush=True)
                self._json(500, {"error": {"message": str(e)[:300]}})

    return H


def main():
    model_dir = os.environ["JULIA_MODEL_DIR"]
    code_dir = os.environ.get("JULIA_CODE_DIR", model_dir)
    model_name = os.environ.get("JULIA_MODEL_NAME", os.path.basename(os.path.normpath(model_dir)))
    max_length = int(os.environ.get("JULIA_MAX_LENGTH", "8192"))
    head_length = int(os.environ.get("JULIA_HEAD_LENGTH", "640"))
    strict = os.environ.get("JULIA_STRICT", "1") == "1"
    sys.path.insert(0, code_dir)
    import torch
    from julia import load_model

    device = os.environ.get("JULIA_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
    t0 = time.time()
    engine = load_model(model_dir, device=device, max_length=max_length, head_length=head_length,
                        strict_encoding=strict)
    load_s = round(time.time() - t0, 1)
    params = sum(p.numel() for p in engine.model.parameters())
    cfg = {"device": str(engine.device), "max_length": max_length, "head_length": head_length,
           "strict_encoding": strict, "backend": engine.transformer_backend, "dtype": "bf16 autocast"
           if engine.device.type == "cuda" else "fp32", "marker_only_head": engine.model.marker_only_head,
           "params": params, "model_dir": model_dir, "torch": torch.__version__,
           "cpu_threads": torch.get_num_threads()}
    import transformers
    cfg["transformers"] = transformers.__version__

    def health():
        h = {"ok": True, "model_name": model_name, "loaded": [model_name], "cfg": cfg, "impl": IMPL,
             "load_s": load_s, "uptime_s": round(time.time() - t0)}
        if engine.device.type == "cuda":
            h["vram_peak_alloc_gib"] = round(torch.cuda.max_memory_allocated(engine.device) / 2**30, 3)
        return h

    handler = make_handler(engine, model_name, strict, health)
    print(f"[julia-service] loaded {model_dir} as '{model_name}' in {load_s}s; cfg={json.dumps(cfg)}", flush=True)
    uds = os.environ.get("JULIA_UDS")
    if uds:
        if os.path.exists(uds):
            os.unlink(uds)
        server = UnixHTTPServer(uds, handler)
        os.chmod(uds, 0o660)
        print(f"[julia-service] listening on unix:{uds}", flush=True)
    else:
        server = ThreadingHTTPServer((os.environ.get("JULIA_HOST", "127.0.0.1"),
                                      int(os.environ.get("JULIA_PORT", "8098"))), handler)
        print(f"[julia-service] listening on {server.server_address}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
