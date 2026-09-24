"""Load the bank, policy and a plan; select trials."""
import json


def load_policy(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_bank(path):
    cases = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                c = json.loads(line)
                cases[c["case_id"]] = c
    return cases


def load_plan(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def select_trials(plan, phases):
    """All plan trials whose phase is in `phases` (e.g. {'pilot','broad'}), in plan order."""
    phases = set(phases)
    return [t for t in plan["trials"] if t["phase"] in phases]


def abstain_key(case, policy):
    wf = policy["workflows"][case["workflow"]]
    k = wf.get("abstain_key")
    if k and k in case["options"]:
        return k
    return "abstain" if "abstain" in case["options"] else None