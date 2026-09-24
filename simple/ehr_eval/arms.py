"""One HTTP caller for any arm, with the SPEC §8 retry policy.

    call_arm(arm, request) -> AttemptResult

arm is a dict describing the endpoint:
    {"kind": "jev",    "url": "https://openrouter.ai/api/alpha/decisions",
     "model": "typesafe/jev-1.13", "api_key_env": "OPENROUTER_API_KEY", "timeout_s": 30}
    {"kind": "jev",    "url": "http://host:8090/v1/decisions", "model": "...", ...}   # self-hosted
    {"kind": "openai", "url": "http://host:8000/v1/chat/completions", "model": "...", ...}

request is what call_arm() turns into the exact wire body:
    {"evidence":..., "options": {...}, "policy_text": ...}

No dependency; urllib only. The API key is read from the environment and never logged.
"""
import json
import random
import time
import urllib.error
import urllib.request

from .prompts import decisions_body_text, openai_body

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
RETRY_BACKOFF_S = [1, 4]          # after attempt 1 and 2; 3 attempts total
JITTER_S = 0.25


class ArmError(Exception):
    """Fatal arm error (401/402/403/404 or budget refused): the caller stops the run."""


def _http_json(url, headers, body_text, timeout_s):
    req = urllib.request.Request(url, data=body_text.encode("utf-8"), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode("utf-8"))
        except Exception:
            parsed = None
        return e.code, parsed


def build_request(arm, evidence, options, policy_text):
    return {"evidence": evidence, "options": options, "policy_text": policy_text}


def call_arm(arm, request, sleep=time.sleep):
    """One (possibly retried) call. Returns a dict:
    {ok, http_status, body, error, latency_ms, attempts, cost_usd}
    Raises ArmError on fatal HTTP statuses (401/402/403/404)."""
    kind = arm["kind"]
    if kind == "jev":
        body_text = decisions_body_text(request["evidence"], request["options"],
                                        request["policy_text"], arm["model"])
    elif kind == "openai":
        body_text = json.dumps(openai_body(request["evidence"], request["options"],
                                           request["policy_text"], arm["model"]),
                               ensure_ascii=False, separators=(",", ":"))
    else:
        raise ValueError("unknown arm kind %r" % kind)

    headers = {"Content-Type": "application/json"}
    key_env = arm.get("api_key_env")
    if key_env:
        import os
        key = os.environ.get(key_env, "")
        if not key:
            raise ArmError("no API key: set the %s environment variable" % key_env)
        headers["Authorization"] = "Bearer " + key

    attempts, cost = 0, None
    t0 = time.monotonic()
    for attempt_no in range(1, 1 + len(RETRY_BACKOFF_S) + 1):
        attempts = attempt_no
        try:
            status, body = _http_json(arm["url"], headers, body_text, arm.get("timeout_s", 60))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            status, body = None, None
            error = "network_error: %s" % e
        else:
            error = None
            if status >= 400:
                error = "http_%d" % status
        if status is not None and status in (401, 402, 403, 404):
            raise ArmError("fatal http_%d from %s" % (status, arm["url"]))
        retryable = error is not None and attempt_no <= len(RETRY_BACKOFF_S) \
            and (status is None or status == 429 or status >= 500)
        if status is not None and 200 <= status < 300 and isinstance(body, dict):
            c = (body.get("usage") or {}).get("cost")
            if isinstance(c, (int, float)):
                cost = c + (0 if cost is None else cost)
            return {"ok": True, "http_status": status, "body": body, "error": None,
                    "latency_ms": int((time.monotonic() - t0) * 1000), "attempts": attempts,
                    "cost_usd": cost}
        if not retryable:
            return {"ok": False, "http_status": status, "body": body, "error": error,
                    "latency_ms": int((time.monotonic() - t0) * 1000), "attempts": attempts,
                    "cost_usd": cost}
        sleep(RETRY_BACKOFF_S[attempt_no - 1] + random.random() * JITTER_S)
    return {"ok": False, "http_status": status, "body": None, "error": error,
            "latency_ms": int((time.monotonic() - t0) * 1000), "attempts": attempts, "cost_usd": cost}