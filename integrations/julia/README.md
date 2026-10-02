# Julia-1 integration

How SupersonicLabs' [Julia-1](https://huggingface.co/SupersonicLabs/Julia-1) (Apache-2.0) was served, fine-tuned
and benchmarked for the `Julia-1` rows in `results/`.

Julia-1 is a 144.3M-parameter decision model: JHU CLSP's
[mmBERT-small](https://huggingface.co/jhu-clsp/mmBERT-small) (a multilingual ModernBERT encoder) plus a small
decision head (2 transformer layers and a scorer). It reads `state`, a question and 2–20 option descriptions in one
sequence and scores one marker token per option, so there is no text generation and nothing to parse. The release's
own Python runtime (`julia/`) already answers the named-question interface
`predict(state=..., questions={"choice": {"type", "instructions", "criteria"}})`, which is the Jev Decisions request
body without `model`. `serve_julia.py` is a thin HTTP shell around it. The only thing it adds is what the Jev wire
format needs: `confidence` (Julia calls it `max_probability`), the response `model`, and `usage`. The harness's
generic `decisions` arm talks to it through `--decisions-path /v1/systemone`.

## 1. Code review and isolation

The release ships custom code: a Python package (`julia/`), an optional native "Bend" router (`julia/router/`:
a generated-C bridge, Bend sources and `build.py`), tests and a benchmark script. Before running anything we read it:

- **Inference path** (`julia/__init__.py`, `inference.py`, `model.py`, `data.py`, `typed.py`,
  `router/engine.py`, `router/encoder.py`): plain PyTorch + transformers. Weights load from safetensors (no
  pickle), the tokenizer and encoder config load with `trust_remote_code=False`. No network calls, no
  subprocesses, no file writes, no dynamic code loading. `router/encoder.py` replaces the ModernBERT forward
  with an inference-only version that uses private transformers internals, which is why the release pins
  `transformers>=5.0,<5.1`.
- **Native router**: `router/build.py` shells out to the `bend` compiler and `clang` to build
  `libjulia_router.so`; `router/native.py` loads it with `ctypes`. It is used only when a Bend backend or Bend
  post-processing is requested explicitly. We never built or loaded it: the default `torch` backend is the
  complete model.
- **`scripts/reproduce_typed.py`** downloads a pinned test parquet with `urllib`. We did not run it.
- `model.save_pretrained` writes files, but only when called (our trainer calls it on its own output dir).

Everything ran on the benchmark server inside Docker with **`--network none`**, as a non-root user, with the
model directory, code and data mounted **read-only** and one scratch directory writable. Because a container
without a network cannot publish a port, the service listens on a unix socket in a mounted directory, and
`socat` on the host bridges `127.0.0.1:<port>` to that socket.

## 2. Setup (Linux, NVIDIA GPU optional)

```bash
hf download SupersonicLabs/Julia-1                       # ~0.6 GB
cp -rL ~/.cache/huggingface/hub/models--SupersonicLabs--Julia-1/snapshots/<rev> Julia-1   # resolve symlinks
# the release pins transformers 5.0.x; install it (not torch) into a separate target dir
docker run --rm -u $(id -u):$(id -g) -e HOME=/tmp -v $PWD/pydeps:/t python:3.12-slim \
  pip install --target /t "transformers==5.0.0"
rm -rf pydeps/numpy pydeps/numpy-*.dist-info pydeps/numpy.libs   # keep the image's numpy
```

Runtime image: `vllm/vllm-openai:v0.30.0` (Python 3.12, torch 2.13 + CUDA 13), used only for its Python and
torch. With transformers 5.17 (the image's own) the release's encoder specialization fails, so the pinned 5.0.0
is put first on `PYTHONPATH`. Revision tested: `a85b127321d580d65176c89ced8273f305745d85`, weights sha256
`df853bf7…ad95b72` (matches `inference-policy.json`).

## 3. Serve

```bash
JULIA_RELEASE=$PWD/Julia-1 JULIA_PYDEPS=$PWD/pydeps NAME=julia-1 GPU=0 PORT=8098 ./serve_julia_docker.sh
# fine-tuned: add JULIA_CKPT=<train out>/best NAME=julia-1-ft-full
```

`serve_julia.py` loads the model with the release's `julia.load_model` (FastEngine, `torch` backend). On CUDA the
release runs the encoder under BF16 autocast. Requests are processed one at a time.

| setting | value | why |
|---|---|---|
| `max_length` | 8192 | release `inference-policy.json`; our longest request is 3,123 tokens |
| `strict_encoding` | on | release `inference-policy.json`: reject a request rather than truncate it |
| `head_length` | **640** (release default 512) | see below |

**Token budget.** Julia puts the question (here: the policy text plus the harness's instruction suffix, about 320
tokens) and every option description into a "head" with a fixed budget, ahead of the state. At the release's
512-token budget, 6.3% of v1 dev requests and 6.5% of v2 core-shard cases (chart requests with 16 candidates,
some 16-option inbox requests) do not fit, and strict encoding rejects them. 640 is the smallest round budget that
fits every request losslessly (max head + options = 567 tokens). No request is truncated at 640, and no other
setting was tuned. Option descriptions are at most 16 tokens (limit 48), and requests have 2–16 options (limit 20).

## 4. Fine-tune on v1 dev only

```bash
node --experimental-strip-types ../clef/build_bodies.ts <repo> harness_bodies_v1dev.jsonl
python prep_julia_data.py Julia-1 <repo> harness_bodies_v1dev.jsonl prep
python train_julia.py --release Julia-1 --data prep/records.jsonl --out ft-full --mode full --lr 3e-5
python train_julia.py --release Julia-1 --data prep/records.jsonl --out ft-head --mode head --lr 3e-4
```

1. **Build request bodies.** `../clef/build_bodies.ts` writes the exact request bodies the harness sends for the
   4,084 v1 **dev** items (the Laya, CLM-8B and Clef training set).
2. **Prepare records.** `prep_julia_data.py` turns each body into the row the service builds from it (state,
   question = instructions, options = criteria descriptions in presented order) plus the gold index. It rebuilds
   every body independently in Python and checks that both encode to the same Julia token ids and option markers
   (4,084 compared, 0 differences). The family-grouped, stratified val split is the Clef procedure unchanged:
   3,652 train and 432 val.
3. **Train.** `train_julia.py` starts from the released weights. `--mode full` trains all 144M parameters;
   `--mode head` freezes the encoder and trains the decision head. AdamW, linear warmup and decay, batch 16,
   cross-entropy over the option scores, BF16 autocast as served, inputs encoded exactly as served. It
   evaluates val twice per epoch, keeps the best checkpoint (val accuracy, ties to lower val NLL) and stops
   early. The checkpoint is written with the release's own `save_pretrained`, plus its tokenizer.

v1 holdout and v2 are never read.

## 5. Benchmark

```bash
DECISIONS_MODEL=julia-1 DECISIONS_EXPECTED_MODEL=julia-1 DECISIONS_PATH=/v1/systemone \
node --experimental-strip-types evals/runner.ts --run-dir runs/julia-1-v1 --plan data/plans/plan_decisions.json \
  --bank data/bank.jsonl --policy policy/policy.json \
  --phases pilot,broad,counterfactual,repeat,load_c1,load_c2,load_c4,load_c8,holdout \
  --concurrency 6 --decisions-base http://127.0.0.1:8098
python3 verify/verify_single.py runs/julia-1-v1 --bank data/bank.jsonl --plan data/plans/plan_decisions.json \
  --policy policy/policy.json --arm decisions --checkpoints julia-1
```

For bank v2, derive a decisions plan with `data/v2/derive_plan.py … --arm decisions`. The server's own Node is
v18, so the harness ran under Node 25 in a container on the same host (`--network host`, repo read-only).

## Tests

```bash
python3 -m unittest discover -s integrations/julia -p 'test_*.py' -v     # offline, no torch needed
JULIA_MODEL_DIR=Julia-1 python3 -m unittest discover -s integrations/julia -p 'test_*.py' -v   # + real model
```

The offline tests check the request-to-Julia mapping (state and questions pass through unchanged, presented
option order kept), the response against the repo's strict decisions validator, error handling (400 for bad
JSON and invalid requests) and the HTTP surface. The real-model test runs a few bank cases through the release.
