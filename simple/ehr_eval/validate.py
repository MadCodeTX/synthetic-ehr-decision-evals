"""Strict response validation (SPEC §6). Invalid answers are results, not errors."""
import json
import re


def validate_decisions(body, option_keys):
    """Jev/decisions-shaped response: answers.choice must be a well-formed choice object."""
    out = {"valid": False, "choice": None, "failure_reason": None}
    fail = lambda r: (out.update(failure_reason=r), out)[1]  # noqa: E731
    ans = (body or {}).get("answers", {}).get("choice") if isinstance(body, dict) else None
    if not isinstance(ans, dict):
        return fail("missing_answers_choice")
    if ans.get("type") != "choice":
        return fail("wrong_type")
    c = ans.get("choice")
    if not isinstance(c, str) or c not in option_keys:
        return fail("choice_not_in_options")
    probs = ans.get("probabilities")
    if not isinstance(probs, dict):
        return fail("probabilities_not_object")
    if set(probs) != set(option_keys):
        return fail("probabilities_missing_key" if any(k not in probs for k in option_keys)
                    else "probabilities_extra_key")
    for v in list(probs.values()) + [ans.get("confidence")]:
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not (0.0 <= float(v) <= 1.0):
            return fail("probability_not_finite_in_0_1")
    if "confidence" not in ans:
        return fail("missing_confidence")
    out.update(valid=True, choice=c)
    return out


def _lenient_choice(content, option_keys):
    if not isinstance(content, str):
        return None
    for m in re.finditer(r'"choice"\s*:\s*"((?:[^"\\]|\\.)*)"', content):
        try:
            k = json.loads('"' + m.group(1) + '"')
        except Exception:
            continue
        if k in option_keys:
            return k
    return None


def validate_openai(content, option_keys):
    """Chat-completions response: strict JSON object with exactly one key 'choice' in options."""
    out = {"valid": False, "choice": None, "failure_reason": None,
           "lenient_choice": _lenient_choice(content, option_keys)}
    fail = lambda r: (out.update(failure_reason=r), out)[1]  # noqa: E731
    if not isinstance(content, str):
        return fail("no_content")
    t = content.strip()
    if not t:
        return fail("empty_content")
    try:
        parsed = json.loads(t)
    except Exception:
        return fail("not_json")
    if not isinstance(parsed, dict):
        return fail("not_object")
    keys = list(parsed.keys())
    if "choice" not in keys:
        return fail("missing_choice_key")
    if len(keys) != 1:
        return fail("extra_keys")
    if not isinstance(parsed["choice"], str) or parsed["choice"] not in option_keys:
        return fail("choice_not_in_options")
    out.update(valid=True, choice=parsed["choice"])
    return out