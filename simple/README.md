# ehr-eval (simple Python client)

A dependency-free (Python 3.9+ stdlib) way to run the bank against **any LLM API** — including
Jev via the OpenRouter Decisions API, or any OpenAI-compatible `chat/completions` endpoint.

If you just want the numbers and can read ~500 lines of Python, use this. The TypeScript harness
under `evals/` is the full-featured version (resume, budget caps, drift checks, health probes);
both produce comparable per-trial records and use identical prompts and validation.

## Layout

| file | what it does |
|---|---|
| `ehr_eval/prompts.py` | exact request bodies (SPEC §5), canonical JSON, `input_hash`/`prompt_hash` |
| `ehr_eval/validate.py` | strict response validation (a wrong answer is invalid, never an error) |
| `ehr_eval/arms.py` | one `call_arm()` function: HTTP + retries (429/5xx/timeout, 2 retries) |
| `ehr_eval/bank.py` | load `bank.jsonl` + a plan; select trials by phase |
| `run_eval.py` | CLI: run a plan against one arm, write `results.jsonl`, `pairs.jsonl`, `summary.json` |
| `tests/test_offline.py` | stdlib `unittest` suite with an in-process mock server (no network) |

## Quick start

```bash
export OPENROUTER_API_KEY=sk-or-...          # or your own env var name, see --api-key-env

# Jev (OpenRouter Decisions API)
python3 simple/run_eval.py \
  --bank data/bank.jsonl --policy policy/policy.json \
  --plan data/plans/plan_jev.json --phases pilot \
  --arm jev --out-dir runs/py-smoke

# Any OpenAI-compatible endpoint (vLLM, OpenRouter, your own router)
python3 simple/run_eval.py \
  --bank data/bank.jsonl --policy policy/policy.json \
  --arm openai --base-url http://localhost:8000/v1 --model your/model \
  --plan data/plans/plan_qwen.json --phases pilot,broad --concurrency 4 --limit 100
```

Output: `runs/py-.../results.jsonl` (one line per arm-trial), `pairs.jsonl`, `summary.json`
(accuracy per workflow, per stratum, abstain share, spend). Invalid responses stay in the
denominators — they count as incorrect, never as errors.

## Arms

| arm kind | endpoint shape | auth |
|---|---|---|
| `jev` | `POST https://openrouter.ai/api/alpha/decisions` (`{model, state, questions}`) | `Authorization: Bearer <key>` |
| `openai` | `POST <base>/chat/completions` (OpenAI-compatible) | optional `Authorization: Bearer <key>` |

`--base-url` + `--model` also let you point the `openai` kind at any self-hosted server
(vLLM, llama.cpp server, your own fine-tune). For a Jev-Decisions-compatible *self-hosted*
service (Laya-style), use `--arm jev --base-url http://your-host:8090` — the request body is
identical except the model id (`--model`), and the strict validator applies verbatim.

## Costs

`usage.cost` (when the host reports one, as OpenRouter does) is summed per call and reported in
`summary.json`. The optional `--max-usd` cap stops new trials once total reported spend crosses it.
No other cap is imposed — that is deliberately left to the caller.
