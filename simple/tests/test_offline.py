"""Offline tests: no network. Uses an in-process HTTP server as a mock arm.

Run:  python3 -m unittest discover -s simple/tests -v   (from the repo root)
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "simple"))

from ehr_eval import prompts as P          # noqa: E402
from ehr_eval import validate as V         # noqa: E402
from ehr_eval.arms import build_request, call_arm  # noqa: E402
from run_eval import main as run_main      # noqa: E402

POLICY_TEXT = "Route the item by these rules."
OPTS = {"needs_human_review": "Needs human review", "medical_records_roi": "Records request"}
EVIDENCE = {"patient_name": "Casey Test", "dob": "1980-01-01"}


class MockHandler(BaseHTTPRequestHandler):
    """Answers /decisions (Jev-shaped) and /chat/completions (OpenAI-shaped) with valid answers."""

    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if self.path.endswith("/decisions"):
            keys = list(body["questions"]["choice"]["criteria"].keys())
            probs = {k: (1.0 if k == keys[0] else 0.0) for k in keys}
            out = {"id": "mock-d", "model": body["model"], "answers": {"choice": {
                "type": "choice", "choice": keys[-1], "probabilities": probs, "confidence": 1.0}},
                "usage": {"cost": 0.00031}}
        elif self.path.endswith("/chat/completions"):
            keys = self.server.option_keys
            out = {"id": "mock-c", "model": body["model"],
                   "choices": [{"message": {"content": json.dumps({"choice": keys[-1]})},
                                "finish_reason": "stop"}],
                   "usage": {"cost": 0.00042}}
        else:
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class TestPrompts(unittest.TestCase):
    def test_instr_spacing(self):
        self.assertTrue(P.instr("Policy.").startswith("Policy. Treat"))
        self.assertTrue(P.instr("Policy.\n").startswith("Policy.\nTreat"))
        self.assertEqual(P.instr("Policy."), "Policy. " + P.INSTR_SUFFIX)

    def test_option_order_preserved(self):
        opts = {"300": "MRN 300", "20": "MRN 20", "needs_human_review": "Human", "7": "MRN 7"}
        pairs = P.option_pairs(opts)
        self.assertEqual([k for k, _ in pairs], ["300", "20", "needs_human_review", "7"])
        self.assertIn('"criteria":{"300":"MRN 300","20":"MRN 20","needs_human_review":"Human","7":"MRN 7"}',
                      P.decisions_body_text(EVIDENCE, opts, POLICY_TEXT, "m"))

    def test_prompt_hash_ignores_model(self):
        r1 = build_request({"kind": "jev"}, EVIDENCE, OPTS, POLICY_TEXT)
        h1 = P.prompt_hash("jev", r1)
        r2 = build_request({"kind": "jev"}, EVIDENCE, OPTS, POLICY_TEXT)
        self.assertEqual(h1, P.prompt_hash("jev", r2))
        body = P.openai_body(EVIDENCE, OPTS, POLICY_TEXT, "model-a")
        body2 = P.openai_body(EVIDENCE, OPTS, POLICY_TEXT, "model-b")
        self.assertNotEqual(body["model"], body2["model"])
        stripped = {k: v for k, v in body.items() if k != "model"}
        self.assertEqual(P.prompt_hash("openai", body),
                         P._sha256(json.dumps(stripped, separators=(",", ":"), ensure_ascii=False)))
        # property (mirrors evals/clients.ts): prompt_hash excludes the model field
        self.assertEqual(P.prompt_hash("openai", body), P.prompt_hash("openai", body2))

    def test_input_hash_shape(self):
        h = P.input_hash("inbox", EVIDENCE, OPTS, POLICY_TEXT)
        self.assertEqual(len(h), 64)


class TestValidate(unittest.TestCase):
    def test_decisions_valid(self):
        keys = list(OPTS)
        body = {"answers": {"choice": {"type": "choice", "choice": "medical_records_roi",
                                       "probabilities": {k: 0.5 for k in keys}, "confidence": 0.9}}}
        self.assertTrue(V.validate_decisions(body, keys)["valid"])

    def test_decisions_extra_probability_key_invalid(self):
        keys = list(OPTS)
        body = {"answers": {"choice": {"type": "choice", "choice": "medical_records_roi",
                                       "probabilities": {**{k: 0.5 for k in keys}, "x": 0.1},
                                       "confidence": 0.9}}}
        self.assertEqual(V.validate_decisions(body, keys)["failure_reason"], "probabilities_extra_key")

    def test_openai_strict_and_lenient(self):
        keys = list(OPTS)
        good = json.dumps({"choice": "medical_records_roi"})
        self.assertTrue(V.validate_openai(good, keys)["valid"])
        prose = 'The answer is {"choice":"needs_human_review"} per policy.'
        v = V.validate_openai(prose, keys)
        self.assertFalse(v["valid"])
        self.assertEqual(v["lenient_choice"], "needs_human_review")
        self.assertEqual(V.validate_openai('{"choice":"nope"}', keys)["failure_reason"],
                         "choice_not_in_options")


class TestEndToEnd(unittest.TestCase):
    def test_run_against_mock_server(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        srv.option_keys = list(OPTS)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = "http://127.0.0.1:%d/v1" % srv.server_address[1]
            tmp = self._make_fixtures()
            for kind, arm in (("openai", "openai"), ("jev", "jev")):
                out = os.path.join(tmp, "out-" + kind)
                rc = run_main([
                    "--bank", os.path.join(tmp, "bank.jsonl"),
                    "--policy", os.path.join(tmp, "policy.json"),
                    "--plan", os.path.join(tmp, "plan.json"),
                    "--phases", "pilot,broad", "--arm", kind,
                    "--model", "mock-model", "--base-url", base if kind == "openai" else base + "/decisions",
                    "--arm-id", arm, "--concurrency", "2", "--out-dir", out,
                ])
                self.assertEqual(rc, 0)
                results = [json.loads(l) for l in open(os.path.join(out, "results.jsonl"))]
                self.assertEqual(len(results), 6)
                self.assertTrue(all(r["valid"] and r["correct"] for r in results),
                                "mock answers are valid and correct")
                summary = json.load(open(os.path.join(out, "summary.json")))
                self.assertEqual(summary["spend_usd"], round(len(results) * (0.00031 if kind == "jev" else 0.00042), 6))
                self.assertEqual(summary["accuracy_by_workflow"]["chart"]["correct"], 6)
        finally:
            srv.shutdown()

    def _make_fixtures(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        policy = {"policy_version": "v1", "workflows": {
            "chart": {"policy_text": POLICY_TEXT, "destinations": OPTS, "abstain_key": "needs_human_review"},
            "inbox": {"policy_text": POLICY_TEXT, "destinations": OPTS, "abstain_key": "needs_human_review"}}}
        with open(os.path.join(tmp, "policy.json"), "w") as fh:
            json.dump(policy, fh)
        with open(os.path.join(tmp, "bank.jsonl"), "w") as fh:
            for i in range(6):
                c = {"case_id": "chart-g-%06d" % i, "workflow": "chart", "split": "dev",
                     "stratum": "s%d" % (i % 2), "family_id": "f%d" % i, "counterfactual_of": None,
                     "changed_fact": None, "perturbations": {}, "evidence": {"n": i},
                     "options": dict(OPTS), "expected": "medical_records_roi", "label_basis": "test",
                     "ambiguity": None, "repeat_panel": False}
                fh.write(json.dumps(c) + "\n")
        plan = {"plan_version": "t", "order_seed": 1, "total_trials": 6,
                "trials": [{"trial_id": "chart-g-%06d#%s#r0" % (i, ph), "case_id": "chart-g-%06d" % i,
                            "phase": ph, "rep": 0, "arm_order": ["jev"]}
                           for i, ph in ((0, "pilot"), (1, "pilot"), (2, "broad"), (3, "broad"),
                                         (4, "broad"), (5, "broad"))]}
        with open(os.path.join(tmp, "plan.json"), "w") as fh:
            json.dump(plan, fh)
        return tmp


if __name__ == "__main__":
    unittest.main()
