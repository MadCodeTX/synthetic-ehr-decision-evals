import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { run, parseArgs } from '../runner.ts';
import { makeMockFetch } from '../mock.ts';
import { baseOpts, tmpDir, readRows, quiet, QWEN_ID, hang, FIX, J } from './helpers.ts';

const deps = (fetch: any, extra: any = {}) => ({ fetch, credential: () => 'sk-or-v1-TESTSECRET', ...quiet, ...extra });

/** Mock that hangs every model call after `after` model calls (health/models still answer). */
function hangingAfter(after: number, onHang?: () => void) {
  const inner = makeMockFetch({ qwenModel: QWEN_ID });
  let modelCalls = 0, hung = 0;
  const f = async (url: string, init: any) => {
    if (init?.method === 'POST') {
      modelCalls++;
      if (modelCalls > after) { hung++; if (hung === 1) onHang?.(); return hang(init.signal); }
    }
    return inner(url, init);
  };
  return Object.assign(f, { modelCalls: () => modelCalls });
}

function checkIntegrity(dir: string) {
  const results = readRows(path.join(dir, 'results.jsonl'));   // throws on any torn line
  const pairs = readRows(path.join(dir, 'pairs.jsonl'));
  const attempts = readRows(path.join(dir, 'attempts.jsonl'));
  const events = readRows(path.join(dir, 'events.jsonl'));
  const byId = new Map(results.map((r) => [r.result_id, r]));
  const ids = pairs.map((p) => p.trial_id);
  assert.equal(new Set(ids).size, ids.length, 'no duplicate trial_ids in pairs');
  for (const p of pairs) {
    const j = byId.get(p.jev_result_id), q = byId.get(p.qwen_result_id);
    assert.ok(j && q, `pair ${p.trial_id} references existing results`);
    assert.equal(j.arm, 'jev'); assert.equal(q.arm, 'qwen'); assert.equal(j.trial_id, p.trial_id); assert.equal(q.trial_id, p.trial_id);
  }
  for (const r of results) {
    const n = attempts.filter((a) => a.trial_id === r.trial_id && a.arm === r.arm && a.segment === r.segment).length;
    assert.equal(r.attempts, n, 'results.attempts == attempt lines for (trial, arm, segment)');
    if (!r.valid) { assert.equal(r.choice, null); assert.equal(r.abstained, false); assert.equal(r.correct, false); }
    if (r.arm === 'qwen') { assert.equal(r.reported_cost_usd, null); assert.equal(r.cost_known, false); }
  }
  for (const a of attempts) if (a.arm === 'qwen') { assert.equal(a.reported_cost_usd, null); assert.equal(a.cost_known, false); }
  const all = ['manifest.json', 'attempts.jsonl', 'results.jsonl', 'pairs.jsonl', 'events.jsonl', 'segments.jsonl', 'status.json']
    .map((f) => fs.existsSync(path.join(dir, f)) ? fs.readFileSync(path.join(dir, f), 'utf8') : '').join('\n');
  assert.ok(!all.includes('TESTSECRET') && !/Bearer\s+sk-/.test(all), 'no credentials in run files');
  return { results, pairs, attempts, events };
}

test('full run on test fixtures: pairs for every selected trial, holdout not run unless listed, load phase forces concurrency', async () => {
  const dir = tmpDir('full');
  const s = await run(baseOpts(dir), deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.equal(s.exitCode, 0); assert.equal(s.stopReason, null);
  const { pairs, results, events } = checkIntegrity(dir);
  assert.equal(pairs.length, 4 + 10 + 8);
  assert.ok(!pairs.some((p) => p.phase === 'holdout'));
  assert.ok(results.filter((r) => r.phase === 'load_c2').every((r) => r.concurrency === 2));
  assert.ok(results.filter((r) => r.phase === 'broad').every((r) => r.concurrency === 3));
  assert.ok(events.some((e) => e.type === 'run_end'));
  // arm order is honoured: first-arm attempt starts no later than second-arm attempt
  const plan = JSON.parse(fs.readFileSync(baseOpts(dir).planPath, 'utf8'));
  for (const t of plan.trials.filter((t: any) => t.phase !== 'holdout')) {
    const [a0, a1] = t.arm_order.map((arm: string) => results.find((r) => r.trial_id === t.trial_id && r.arm === arm));
    assert.ok(a0.t_end_utc <= a1.t_start_utc, `sequential arm order for ${t.trial_id}`);
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  assert.equal(manifest.arms.qwen.pinned_model, QWEN_ID);
  assert.ok(Object.keys(manifest.harness_files).some((f) => f.endsWith('runner.ts')));
  assert.ok(manifest.hashes.policy_hash && manifest.hashes.fixture_hash && manifest.hashes.plan_hash && manifest.deadline_utc);
  const status = JSON.parse(fs.readFileSync(path.join(dir, 'status.json'), 'utf8'));
  assert.equal(status.final, true); assert.equal(status.completed_pairs_total, 22);

  // holdout only when listed
  const dir2 = tmpDir('holdout');
  await run(baseOpts(dir2, { phases: ['holdout'] }), deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.equal(readRows(path.join(dir2, 'pairs.jsonl')).length, 2);
});

test('forced stop via STOP file mid-flight: no new dispatch, in-flight aborted, files parse, no pair for incomplete trials', async () => {
  const dir = tmpDir('stopfile');
  const f = hangingAfter(9, () => fs.writeFileSync(path.join(dir, 'STOP'), ''));
  const t0 = Date.now();
  const s = await run(baseOpts(dir), deps(f));
  assert.ok(Date.now() - t0 < 5000);
  assert.equal(s.exitCode, 0); assert.match(s.stopReason!, /STOP/);
  const { pairs, attempts, events, results } = checkIntegrity(dir);
  const aborted = attempts.filter((a) => a.error === 'aborted');
  assert.ok(aborted.length >= 1, 'in-flight attempts recorded as aborted');
  for (const a of aborted) assert.ok(!pairs.some((p) => p.trial_id === a.trial_id), 'no pair for aborted trial');
  assert.ok(f.modelCalls() <= 9 + 3, 'at most `concurrency` hung calls; nothing dispatched after stop');
  const stopT = events.find((e) => e.type === 'stop_requested').t_utc;
  assert.ok(!attempts.some((a) => a.t_start_utc > stopT), 'no attempt started after stop');
  assert.ok(events.some((e) => e.type === 'run_end'));
  assert.ok(pairs.length < 22 && results.length >= pairs.length * 2);
});

test('forced stop via AbortController (signal) mid-flight ⇒ exit 130, aborted attempts, no partial pairs', async () => {
  const dir = tmpDir('signal');
  const ctl = new AbortController();
  const f = hangingAfter(6, () => setTimeout(() => ctl.abort('SIGINT'), 10));
  const s = await run(baseOpts(dir), deps(f, { signal: ctl.signal }));
  assert.equal(s.exitCode, 130);
  const { attempts, pairs, events } = checkIntegrity(dir);
  assert.ok(attempts.some((a) => a.error === 'aborted'));
  assert.ok(events.some((e) => e.type === 'signal' && e.segment === 1));
  for (const p of pairs) assert.ok(!attempts.some((a) => a.trial_id === p.trial_id && a.error === 'aborted'));
});

test('resume: stop, resume ⇒ no duplicate pairs, budget restored, manifest untouched, segment 2 recorded', async () => {
  const dir = tmpDir('resume');
  await run(baseOpts(dir), deps(hangingAfter(11, () => fs.writeFileSync(path.join(dir, 'STOP'), ''))));
  const manifestBefore = fs.readFileSync(path.join(dir, 'manifest.json'));
  const pairs1 = readRows(path.join(dir, 'pairs.jsonl'));
  const att1 = readRows(path.join(dir, 'attempts.jsonl'));
  // Resume refuses while STOP exists? It stops immediately; remove STOP as documented.
  fs.unlinkSync(path.join(dir, 'STOP'));
  // Simulate a torn trailing line from a crash.
  fs.appendFileSync(path.join(dir, 'events.jsonl'), '{"type":"torn');
  const s2 = await run(baseOpts(dir), deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.equal(s2.segment, 2); assert.equal(s2.stopReason, null);
  assert.deepEqual(fs.readFileSync(path.join(dir, 'manifest.json')), manifestBefore, 'manifest untouched');
  const pairs = readRows(path.join(dir, 'pairs.jsonl'));
  assert.equal(pairs.length, 22); assert.equal(new Set(pairs.map((p) => p.trial_id)).size, 22);
  for (const p of pairs1) assert.equal(pairs.find((q) => q.trial_id === p.trial_id).jev_result_id, p.jev_result_id, 'completed trials not re-run');
  const segs = readRows(path.join(dir, 'segments.jsonl'));
  assert.deepEqual(segs.filter((x) => x.type === 'segment_start').map((x) => x.segment), [1, 2]);
  const attempts = readRows(path.join(dir, 'attempts.jsonl'));
  const expectedCommitted = attempts.filter((a) => a.arm === 'jev').reduce((s, a) => s + (a.cost_known ? a.reported_cost_usd : a.reserved_usd), 0);
  const status = JSON.parse(fs.readFileSync(path.join(dir, 'status.json'), 'utf8'));
  assert.ok(Math.abs(status.spend.committed_usd - expectedCommitted) < 1e-9, `budget restored across segments ${status.spend.committed_usd} vs ${expectedCommitted}`);
  assert.equal(status.trials_admitted, new Set(attempts.map((a) => a.trial_id)).size);
  assert.ok(att1.length < attempts.length);
  const ev = fs.readFileSync(path.join(dir, 'events.jsonl'), 'utf8').split('\n');
  assert.ok(ev.some((l) => l === '{"type":"torn'), 'torn line isolated on its own line');
  assert.ok(readRows(path.join(dir, 'results.jsonl')).length >= 44);
  // resume with a changed fixture is refused
  const bank2 = path.join(tmpDir('bank2'), 'bank.jsonl');
  fs.writeFileSync(bank2, fs.readFileSync(baseOpts(dir).bankPath, 'utf8') + '\n');
  await assert.rejects(run(baseOpts(dir, { bankPath: bank2 }), deps(makeMockFetch({ qwenModel: QWEN_ID }))), /resume refused: fixture_hash/);
});

test('resume: trial cap and USD cap span segments', async () => {
  const dir = tmpDir('caps');
  await run(baseOpts(dir, { maxTrials: 5 }), deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.equal(readRows(path.join(dir, 'pairs.jsonl')).length, 5);
  const s2 = await run(baseOpts(dir, { maxTrials: 5 }), deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.match(s2.stopReason!, /max trials/);
  assert.equal(readRows(path.join(dir, 'pairs.jsonl')).length, 5, 'no new trials admitted in segment 2');
  const dir2 = tmpDir('usd');
  const s3 = await run(baseOpts(dir2, { maxUsd: 0.002 }), deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.match(s3.stopReason!, /USD/);
  const att = readRows(path.join(dir2, 'attempts.jsonl')).filter((a) => a.arm === 'jev');
  const committed = att.reduce((s, a) => s + (a.cost_known ? a.reported_cost_usd : a.reserved_usd), 0);
  assert.ok(committed <= 0.002 + 1e-12, `spend ${committed} within cap`);
  checkIntegrity(dir2);
});

test('drift: /v1/models changes mid-run ⇒ drift event + stop', async () => {
  const dir = tmpDir('drift-models');
  let probes = 0;
  const f = makeMockFetch({ qwenModel: QWEN_ID, latencyMs: [15, 30], modelsOverride: () => (++probes > 1 ? ['Qwen/Other-Model'] : null) });
  const s = await run(baseOpts(dir, { healthIntervalMs: 60 }), deps(f));
  assert.match(s.stopReason!, /drift/);
  const { events, pairs } = checkIntegrity(dir);
  const d = events.find((e) => e.type === 'drift');
  assert.equal(d.source, 'models_endpoint'); assert.deepEqual(d.served, ['Qwen/Other-Model']); assert.equal(d.segment, 1);
  assert.ok(pairs.length < 22);
});

test('drift: response `model` differs ⇒ drift event + stop, no pair for that trial', async () => {
  const dir = tmpDir('drift-resp');
  let n = 0;
  const f = makeMockFetch({ qwenModel: QWEN_ID, qwenModelOverride: () => (++n > 4 ? 'Qwen/Swapped' : null) });
  const s = await run(baseOpts(dir, { concurrency: 1 }), deps(f));
  assert.match(s.stopReason!, /drift/);
  const { events, pairs, attempts } = checkIntegrity(dir);
  const d = events.find((e) => e.type === 'drift');
  assert.equal(d.source, 'response_model'); assert.equal(d.served, 'Qwen/Swapped');
  assert.ok(!pairs.some((p) => p.trial_id === d.trial_id));
  assert.equal(pairs.length, 4);
  assert.ok(attempts.some((a) => a.api_model === 'Qwen/Swapped'));
});

test('startup: served model != expected ⇒ refuse before manifest or any model call', async () => {
  const dir = tmpDir('pin');
  const f = makeMockFetch({ qwenModel: 'Qwen/Wrong' });
  await assert.rejects(run(baseOpts(dir), deps(f)), /startup check failed/);
  assert.ok(!fs.existsSync(path.join(dir, 'manifest.json')));
  assert.ok(!f.calls.some((c) => c.method === 'POST'));
});

test('dry-run uses the offline mock and never needs a credential', async () => {
  const dir = tmpDir('dry');
  const s = await run(baseOpts(dir, { dryRun: true, backoffScale: 0 }), { credential: () => { throw new Error('must not read credential'); }, fetch: (() => { throw new Error('must not use real fetch'); }) as any, ...quiet });
  assert.equal(s.exitCode, 0);
  const { results } = checkIntegrity(dir);
  assert.ok(results.some((r) => !r.valid) && results.some((r) => r.valid));
  assert.ok(results.some((r) => r.arm === 'jev' && r.cost_known === false));
});

test('CLI arg parsing', () => {
  const o = parseArgs(['--run-dir', '/tmp/x', '--plan', 'p', '--bank', 'b', '--policy', 'y', '--phases', 'pilot,broad', '--concurrency', '4', '--max-usd', '5', '--dry-run', '--limit', '3']);
  assert.deepEqual(o.phases, ['pilot', 'broad']); assert.equal(o.concurrency, 4); assert.equal(o.dryRun, true); assert.equal(o.limit, 3);
  assert.throws(() => parseArgs(['--run-dir', 'x', '--bogus', '1']), /unknown option|required/);
});

test('decisions arm: full mock run — one result per trial, checkpoint drift-set, no credential, decisions manifest arm', async () => {
  const dir = tmpDir('decisions-full');
  const opts = baseOpts(dir, { planPath: path.join(FIX, 'plan-laya.json'), phases: ['pilot', 'broad', 'load_c2'] });
  const s = await run(opts, deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.equal(s.exitCode, 0); assert.equal(s.stopReason, null);
  const results = readRows(path.join(dir, 'results.jsonl'));
  const pairs = readRows(path.join(dir, 'pairs.jsonl'));
  const attempts = readRows(path.join(dir, 'attempts.jsonl'));
  assert.equal(results.length, pairs.length, 'single-arm run: one result per pair');
  assert.ok(results.every((r) => r.arm === 'decisions'));
  assert.ok(results.every((r) => r.api_model === 'english'), 'routed checkpoint recorded and in the drift set');
  assert.ok(results.every((r) => r.probabilities !== null && r.confidence !== null), 'Jev-shaped probabilities/confidence recorded for calibration');
  assert.ok(results.every((r) => r.cost_known === false && r.charged_usd === null));
  assert.ok(attempts.every((a) => a.arm === 'decisions' && a.reserved_usd === 0), 'no budget reservation on the free local arm');
  assert.ok(pairs.every((p) => p.decisions_result_id && p.decisions_choice !== undefined && p.agree === undefined), 'decisions pair fields, no cross-arm agree');
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  assert.equal(manifest.arms.decisions.repo, 'convaiinnovations/laya');
  assert.equal(manifest.arms.decisions.base_url, 'http://mockhost:8090');
  assert.ok(!manifest.arms.qwen, 'no qwen arm recorded for a decisions-only plan');
  const status = JSON.parse(fs.readFileSync(path.join(dir, 'status.json'), 'utf8'));
  assert.equal(status.completed_pairs_total, pairs.length);
  assert.equal(status.last_health.decisions.ok, true);
  const all = ['manifest.json','attempts.jsonl','results.jsonl','pairs.jsonl','events.jsonl'].map((f) => fs.readFileSync(path.join(dir, f), 'utf8')).join('\n');
  assert.ok(!all.includes('sk-or-v1-TESTSECRET'), 'credential never touched or recorded');
  const events = readRows(path.join(dir, 'events.jsonl'));
  assert.ok(events.some((e) => e.type === 'decisions_checkpoints' && Array.isArray(e.expected_set)));
});

test('decisions arm: response checkpoint outside the drift set ⇒ drift event + stop', async () => {
  const dir = tmpDir('dec-drift');
  let n = 0;
  const f = makeMockFetch({ qwenModel: QWEN_ID, decisionsModel: () => (++n > 3 ? 'english-gpu-fine-tune' : null) });
  const s = await run(baseOpts(dir, { planPath: path.join(FIX, 'plan-laya.json'), concurrency: 1 }), deps(f));
  assert.match(s.stopReason!, /drift/);
  const events = readRows(path.join(dir, 'events.jsonl'));
  const d = events.find((e) => e.type === 'drift');
  assert.equal(d.source, 'response_model'); assert.equal(d.served, 'english-gpu-fine-tune'); assert.equal(d.arm, 'decisions');
});

test('decisions arm: startup health not ok ⇒ refuse before manifest or any model call', async () => {
  const dir = tmpDir('dec-health');
  const inner = makeMockFetch({ qwenModel: QWEN_ID });
  const f = async (url: string, init: any) => url.endsWith('/health') ? J(200, { ok: false, loaded: null }) : inner(url, init);
  await assert.rejects(run(baseOpts(dir, { planPath: path.join(FIX, 'plan-laya.json') }), deps(f)), /decisions startup check failed/);
  assert.ok(!fs.existsSync(path.join(dir, 'manifest.json')));
});

test('arm_order validation: mixed or unknown decisions orders rejected', async () => {
  const plan = JSON.parse(fs.readFileSync(path.join(FIX, 'plan.json'), 'utf8'));
  const bad = path.join(tmpDir('badplan'), 'plan.json');
  plan.trials[0].arm_order = ['decisions', 'qwen'];
  fs.writeFileSync(bad, JSON.stringify(plan));
  await assert.rejects(run(baseOpts(tmpDir('badplan-run'), { planPath: bad }), deps(makeMockFetch({ qwenModel: QWEN_ID }))), /bad arm_order/);
});

test('deadline: dispatch drains before deadline-margin; hard deadline aborts in-flight', async () => {
  const dir = tmpDir('deadline');
  const deadline = new Date(Date.now() + 400).toISOString();
  const s = await run(baseOpts(dir, { deadline, deadlineMarginMs: 0 }), deps(hangingAfter(5)));
  assert.match(s.stopReason!, /deadline/);
  const { attempts, events } = checkIntegrity(dir);
  assert.ok(events.some((e) => e.type === 'deadline'));
  assert.ok(attempts.some((a) => a.error === 'aborted'));
  assert.ok(!attempts.some((a) => Date.parse(a.t_start_utc) > Date.parse(deadline)));
  const dir2 = tmpDir('deadline2');
  const s2 = await run(baseOpts(dir2, { deadline: new Date(Date.now() + 60_000).toISOString(), deadlineMarginMs: 120_000 }), deps(makeMockFetch({ qwenModel: QWEN_ID })));
  assert.match(s2.stopReason!, /dispatch margin/);
  assert.equal(readRows(path.join(dir2, 'attempts.jsonl')).length, 0);
});

test('missing qwen base URL ⇒ clear error before any call', async () => {
  const dir = tmpDir('no-qwen-base');
  await assert.rejects(run(baseOpts(dir, { qwenBase: undefined }), deps(makeMockFetch({ qwenModel: QWEN_ID }))), /qwen arm in the plan but no endpoint/);
});

test('missing decisions base URL ⇒ clear error before any call', async () => {
  const dir = tmpDir('no-dec-base');
  await assert.rejects(run(baseOpts(dir, { decisionsBase: undefined, planPath: path.join(FIX, 'plan-laya.json') }), deps(makeMockFetch({ qwenModel: QWEN_ID }))), /decisions arm in the plan but no endpoint/);
});

test('qwen on openrouter.ai: Authorization header sent, usage.cost counted in budget, model drift-checked', async () => {
  const dir = tmpDir('qwen-openrouter');
  // dryRun:false with the mock as the injected fetch; openrouter base forces the billed path.
  const f = makeMockFetch({ qwenModel: QWEN_ID });
  const s = await run(baseOpts(dir, {
    dryRun: false, qwenBase: 'https://openrouter.ai/api/v1', qwenModel: QWEN_ID, expectQwenId: null,
  }), deps(f, { fetch: f }));
  assert.equal(s.exitCode, 0);
  const attempts = readRows(path.join(dir, 'attempts.jsonl')).filter((a: any) => a.arm === 'qwen');
  assert.ok(attempts.length > 0);
  assert.ok(attempts.every((a: any) => a.cost_known === true && Math.abs(a.reported_cost_usd - 0.00042) < 1e-12), 'usage.cost recorded per attempt');
  const results = readRows(path.join(dir, 'results.jsonl')).filter((r: any) => r.arm === 'qwen');
  assert.ok(results.every((r: any) => r.cost_known === true), 'qwen cost_known on the billed path');
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  assert.equal(manifest.arms.qwen.host, 'openrouter.ai');
  assert.equal(manifest.arms.qwen.pinned_model, QWEN_ID);
  const status = JSON.parse(fs.readFileSync(path.join(dir, 'status.json'), 'utf8'));
  assert.ok(status.spend.committed_usd > 0, 'qwen spend counted in the budget');
});
