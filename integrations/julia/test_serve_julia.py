"""Tests for serve_julia.py.

Offline unit tests use a fake engine with the release's `predict(state=, questions=)` answer shape, and check
the service's responses with the repo's strict decisions validator (simple/ehr_eval/validate.py). No torch
needed:
    python3 -m unittest discover -s integrations/julia -p 'test_*.py' -v      (from the repo root)

Set JULIA_MODEL_DIR (a Julia-1 checkpoint whose `julia/` package is importable, plus torch and transformers
5.0) to also run a real-model smoke over a few harness-shaped requests built from data/bank.jsonl.
"""
import json
import math
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "simple"))

import serve_julia as S  # noqa: E402
from ehr_eval import prompts as P  # noqa: E402
from ehr_eval.validate import validate_decisions  # noqa: E402


class FakeEngine:
    """Mimics julia.typed.predict_typed: scores = position in criteria; raises like the release."""

    def __init__(self):
        self.calls = []

    def predict(self, rows=None, questions=None, *, state=None):
        self.calls.append((state, questions))
        answers = {}
        for qid, q in questions.items():
            if q.get("type") != "choice" or not isinstance(q.get("criteria"), dict):
                raise ValueError("Unsupported question type")
            if not isinstance(q.get("instructions"), str):
                raise ValueError("JSONL line 1: state must be text/JSON and question must be text")
            keys = list(q["criteria"])
            if not 2 <= len(keys) <= 20:
                raise ValueError("JSONL line 1: options must contain 2–20 nonempty rendered descriptions")
            z = [float(i) for i in range(len(keys))]
            m = max(z)
            p = [math.exp(x - m) for x in z]
            t = sum(p)
            p = [x / t for x in p]
            answers[qid] = {"type": "choice", "probabilities": dict(zip(keys, p)), "choice": keys[-1],
                            "max_probability": max(p)}
        return {"answers": answers}

    def encoding_info(self, rows):
        return [{"tokens": 100 + len(r["options"])} for r in rows]


def body_for(case, policy, model="julia-1"):
    text = P.decisions_body_text(case["evidence"], case["options"],
                                 policy["workflows"][case["workflow"]]["policy_text"], model)
    return text, list(case["options"])


def load_cases(n_per_wf=2):
    out, seen = [], {}
    with open(os.path.join(ROOT, "data", "bank.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            if c.get("ambiguity") or seen.get(c["workflow"], 0) >= n_per_wf:
                continue
            seen[c["workflow"]] = seen.get(c["workflow"], 0) + 1
            out.append(c)
            if len(out) >= 3 * n_per_wf:
                break
    return out


POLICY = json.load(open(os.path.join(ROOT, "policy", "policy.json"), encoding="utf-8"))


class AnswerMapping(unittest.TestCase):
    def test_wire_body_passes_through_unchanged(self):
        eng = FakeEngine()
        for c in load_cases():
            text, keys = body_for(c, POLICY)
            body = json.loads(text)
            out = S.answer(eng, body, "julia-1")
            state, questions = eng.calls[-1]
            self.assertEqual(state, body["state"])
            self.assertEqual(questions, body["questions"])
            self.assertEqual(list(questions["choice"]["criteria"]), keys)  # presented order kept
            v = validate_decisions(out, keys)
            self.assertTrue(v["valid"], v)
            self.assertEqual(out["model"], "julia-1")
            ans = out["answers"]["choice"]
            self.assertEqual(ans["confidence"], ans["probabilities"][ans["choice"]])
            self.assertEqual(out["usage"]["input_tokens"], 100 + len(keys))

    def test_noul_confidence(self):
        class E:
            def predict(self, state=None, questions=None):
                return {"answers": {"q": {"type": "noul", "probabilities": {"false": 0.8, "true": 0.2}, "noul": 0.2}}}
        out = S.answer(E(), {"state": "s", "questions": {"q": {"type": "noul", "instructions": "x"}}}, "m", strict=False)
        self.assertAlmostEqual(out["answers"]["q"]["confidence"], 0.8)

    def test_bad_bodies_raise_value_error(self):
        for bad in ([], {"questions": {}}, {"state": {}, "questions": {}}, {"state": {}, "questions": None}):
            with self.assertRaises(ValueError):
                S.answer(FakeEngine(), bad, "m")

    def test_question_rows_mirror_typed(self):
        rows = S.question_rows({"a": 1}, {
            "c": {"type": "choice", "instructions": "i", "criteria": {"x": "X", "y": "Y"}},
            "s": {"type": "score", "instructions": "i", "criteria": ["lo", "hi"]},
            "n": {"type": "noul", "instructions": "i", "criteria": {"true": "T", "false": "F"}}})
        self.assertEqual([r["options"] for r in rows], [["X", "Y"], ["lo", "hi"], ["F", "T"]])


class HttpSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        h = S.make_handler(FakeEngine(), "julia-1", True, lambda: {"ok": True, "loaded": ["julia-1"], "cfg": {}})
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def post(self, path, data):
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_health(self):
        with urllib.request.urlopen(self.base + "/health", timeout=10) as r:
            h = json.loads(r.read())
        self.assertTrue(h["ok"])
        self.assertEqual(h["loaded"], ["julia-1"])
        self.assertIn("stats", h)

    def test_systemone_and_alias(self):
        c = load_cases(1)[0]
        text, keys = body_for(c, POLICY)
        for path in ("/v1/systemone", "/v1/decisions"):
            code, out = self.post(path, text.encode())
            self.assertEqual(code, 200)
            self.assertTrue(validate_decisions(out, keys)["valid"])
            self.assertEqual(out["service"]["served_model"], "julia-1")

    def test_errors(self):
        self.assertEqual(self.post("/v1/systemone", b"{not json")[0], 400)
        code, out = self.post("/v1/systemone", json.dumps({"state": {}, "questions": {"choice": {
            "type": "choice", "instructions": "i", "criteria": {"only": "one"}}}}).encode())
        self.assertEqual(code, 400)
        self.assertIn("2–20", out["error"]["message"])
        self.assertEqual(self.post("/nope", b"{}")[0], 404)


@unittest.skipUnless(os.environ.get("JULIA_MODEL_DIR"), "set JULIA_MODEL_DIR for the real-model smoke")
class RealModelSmoke(unittest.TestCase):
    def test_bank_cases(self):
        d = os.environ["JULIA_MODEL_DIR"]
        sys.path.insert(0, os.environ.get("JULIA_CODE_DIR", d))
        from julia import load_model
        eng = load_model(d, device=os.environ.get("JULIA_DEVICE", "cpu"), max_length=8192,
                         head_length=int(os.environ.get("JULIA_HEAD_LENGTH", "640")), strict_encoding=True)
        for c in load_cases(3):
            text, keys = body_for(c, POLICY)
            out = S.answer(eng, json.loads(text), "julia-1")
            v = validate_decisions(out, keys)
            self.assertTrue(v["valid"], v)
            p = out["answers"]["choice"]["probabilities"]
            self.assertAlmostEqual(sum(p.values()), 1.0, places=5)
            print(c["case_id"], c["expected"], "->", v["choice"], round(out["answers"]["choice"]["confidence"], 3),
                  out["usage"]["input_tokens"], "tok", flush=True)


if __name__ == "__main__":
    unittest.main()
