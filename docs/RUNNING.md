# Running the benchmark on a locked-down work computer

Everything here works without admin rights, through a corporate proxy, with no dependencies beyond
Node itself and the Python interpreter that is already installed.

## 1. Install Node and Python (no admin required)

- **Node**: download the official **ZIP distribution** (`node-v22.x-win-x64.zip` / `.pkg` or `.tar.gz` on
  macOS), unzip it anywhere you can write (e.g. `%USERPROFILE%\tools\node`), and add that folder to your
  user `PATH`. Verify with `node --version` (needs >= 22.6 for TypeScript type-stripping).
- **Python**: the `py` launcher is preinstalled on modern Windows; on macOS `python3` is present.
  Everything Python here is stdlib-only — nothing to pip-install. Verify with `py -3 --version` (Windows)
  or `python3 --version` (Mac/Linux).

## 2. Proxy and corporate TLS

Many corporate proxies inspect TLS. The harness uses plain `fetch`, so:

- If the proxy is set via environment (`HTTPS_PROXY` / `HTTP_PROXY`), recent Node versions honor it when
  `NODE_USE_ENV_PROXY=1` is set. `npm run doctor` checks this and tells you if it is missing.
  Run `npm run doctor -- --network` to verify a request actually gets through.
- If the proxy replaces certificates, export the corporate root CA to a `.pem` file and set
  `NODE_EXTRA_CA_CERTS=C:\path\to\corp-root.pem` (Windows) or the equivalent path (Mac).
- If your proxy requires credentials in the URL, put them in the env vars as your proxy documentation
  describes; never put them in this repo.

## 3. Put the key in `.env`

Copy `.env.example` to `.env` and fill in what you use:

```
OPENROUTER_API_KEY=sk-or-v1-...   # only needed for the Jev (jev) arm
QWEN_BASE_URL=...                 # any OpenAI-compatible /v1 base URL
QWEN_MODEL=...                    # e.g. an OpenRouter model id, or a local vLLM model
DECISIONS_BASE_URL=...            # optional: self-hosted decisions endpoint
```

`.env` is git-ignored. Never commit it. The key can also be exported as the `OPENROUTER_API_KEY`
environment variable instead. The harness reads keys only from these two places, prints at most the
first 8 characters, and never logs keys or headers.

## 4. First run: keep it small

```bash
npm test                                   # offline unit tests (no network)
npm run doctor                             # offline environment check
node --experimental-strip-types evals/runner.ts --run-dir runs/smoke \
  --plan data/plans/plan_jev-qwen.json --bank data/bank.jsonl --policy policy/policy.json \
  --phases pilot --concurrency 2 --limit 6 \
  --qwen-base "$QWEN_BASE_URL" --qwen-model "$QWEN_MODEL"
```

You should get 6 pairs in `runs/smoke/pairs.jsonl`. Inspect `status.json` for spend and rates.

On Windows PowerShell the same command works with `node.exe` and forward-slash paths in quotes.

## 5. Full runs, phases, and arms

Phases: `pilot`, `broad`, `counterfactual`, `repeat`, `load_c1`, `load_c2`, `load_c4`, `load_c8`, `holdout`.

- Paired run: `--plan data/plans/plan_jev-qwen.json` (needs the OpenRouter key and a Qwen endpoint).
- Single arm: `--plan data/plans/plan_jev.json` (key only), `plan_qwen.json` (Qwen endpoint only,
  pass `--qwen-base` and `--qwen-model`), or `plan_decisions.json` (pass `--decisions-base`,
  or the legacy alias `--laya-base`). `--qwen-model`/`QWEN_MODEL` is required when the Qwen base is
  `openrouter.ai`.
- Holdout is only dispatched when `holdout` is listed in `--phases`.
- Default `--run-dir` is `runs/<arms>-<UTC timestamp>`; results land in `runs/` (git-ignored).

## 6. Budget, deadline, stopping, resume

- `--max-usd 5` (default 5) caps API spend; unknown-cost attempts are charged at a reservation, never zero.
- Deadline defaults to now + 24 h; dispatch stops a short safety margin before it, and in-flight calls are
  aborted at the hard deadline. Pass an explicit `--deadline <ISO>` to override.
- **Stop**: create a file named `STOP` in the run directory. Dispatch stops immediately, in-flight calls
  are aborted, files are flushed and closed cleanly. Remove the file before resuming.
- **Resume**: re-running the same command with the same `--run-dir` resumes: completed pairs are skipped,
  budget/spend is restored from `attempts.jsonl`, and the manifest is never rewritten. A changed bank,
  policy or plan triggers a clear refusal; harness file changes are recorded in `segments.jsonl`.
- `--max-trials` caps admitted trials (SPEC: <= 10,000 per launch).

## 7. Verify and analyze

```bash
python3 verify/verify_paired.py runs/<run> --bank data/bank.jsonl --plan data/plans/plan_jev-qwen.json --policy policy/policy.json
python3 verify/verify_single.py runs/<run> --bank data/bank.jsonl --plan data/plans/plan_decisions.json --policy policy/policy.json --arm decisions
python3 analysis/analyze.py runs/<run> --bank data/bank.jsonl --out metrics
```

On Windows use `py -3` instead of `python3`. The verifiers recompute every hash, validity decision and
correctness flag from the raw files and exit non-zero on any hard failure.

## 8. Verify the data you received

`SHA256SUMS` covers `data/**` and `policy/policy.json`; `shasum -a 256 -c SHA256SUMS` (macOS/Linux) or
`Get-FileHash` + comparison (PowerShell) must match. On Windows, git is configured via `.gitattributes`
to keep the data files byte-identical (no line-ending rewriting). Regenerate checksums with
`npm run checksums`; regenerate the bank itself with `python3 generator/generate.py --seed 20260923 --out data`.
