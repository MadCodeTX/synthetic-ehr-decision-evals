"""ehr-eval: a tiny, dependency-free client for the synthetic EHR decision bank.

Modules:
  prompts.py   exact request bodies (SPEC §5) + canonical JSON + hashes
  validate.py  strict response validation
  arms.py      call_arm(): one HTTP call with retries, for any arm
  bank.py      load bank/policy/plan, select trials
  run_eval.py  (script) the CLI

All stdlib, Python 3.9+. Prompts and validation are byte-compatible with the
TypeScript harness in ../evals (SPEC §5-§6), so results are directly comparable.
"""

__version__ = "1.0.0"
