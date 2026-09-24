import test from 'node:test';
import assert from 'node:assert/strict';
import { Budget } from '../budget.ts';
import { callWithRetry, buildJevBody, jevBodyText, buildQwenBody, qwenUserMessage, instr, promptHash, JEV_RESERVE_USD, INSTR_SUFFIX } from '../clients.ts';
import { extractOptionPairs } from '../canon.ts';
import { J, hang } from './helpers.ts';

const far = Date.parse('2035-01-01T00:00:00Z');
function scripted(steps: ((init: any) => Promise<Response>)[]) {
  let i = 0; const seen: any[] = [];
  const f = async (_url: string, init: any) => { seen.push(init); const s = steps[Math.min(i++, steps.length - 1)]; return s(init); };
  return Object.assign(f, { seen, count: () => i });
}
const okJev = (cost?: number) => async () => J(200, { id: 'd1', model: 'typesafe/jev-1.13', answers: { choice: { type: 'choice', choice: 'a', probabilities: { a: 1 }, confidence: 1 } }, usage: cost === undefined ? {} : { cost } });
const base = (fetch: any, budget: Budget | null, extra: any = {}) => ({
  arm: 'jev' as const, url: 'https://x', headers: { Authorization: 'Bearer sk-or-secret' }, body: { q: 1 }, timeoutMs: 1000,
  runSignal: new AbortController().signal, fetch, budget, reserveUsd: budget ? JEV_RESERVE_USD : 0,
  trial_id: 't1', concurrency: 2, segment: 1, phase: 'pilot', onAttempt: () => {}, backoffScale: 0, ...extra,
});

test('retry: 429 then 200 ⇒ 2 attempts, both charged for Jev', async () => {
  const b = new Budget({ maxUsd: 1, maxTrials: 10, deadlineMs: far });
  const recs: any[] = [];
  const f = scripted([async () => J(429, { error: { message: 'slow down' } }), okJev(0.0002)]);
  const out = await callWithRetry(base(f, b, { onAttempt: (a: any) => recs.push(a) }));
  assert.equal(out.kind, 'final'); assert.equal(out.http_status, 200); assert.equal(recs.length, 2);
  assert.equal(recs[0].http_status, 429); assert.equal(recs[0].retryable, true); assert.equal(recs[0].cost_known, false); assert.equal(recs[0].charged_usd, JEV_RESERVE_USD);
  assert.equal(recs[1].cost_known, true); assert.equal(recs[1].reported_cost_usd, 0.0002); assert.equal(recs[1].attempt_no, 2);
  const s = b.snapshot();
  assert.ok(Math.abs(s.committed_usd - (JEV_RESERVE_USD + 0.0002)) < 1e-12); assert.equal(s.reservations_made, 2);
  for (const r of recs) assert.ok(!JSON.stringify(r).includes('sk-or-secret'), 'no secrets in attempt records');
});

test('retry: timeout ⇒ retried; 5xx exhausted after 2 retries; network error retried', async () => {
  let recs: any[] = [];
  let f = scripted([(init) => hang(init.signal), okJev(0.0001)]);
  let out = await callWithRetry(base(f, null, { timeoutMs: 30, onAttempt: (a: any) => recs.push(a) }));
  assert.equal(out.kind, 'final'); assert.equal(recs.length, 2); assert.equal(recs[0].error, 'timeout'); assert.equal(recs[0].retryable, true);

  recs = [];
  f = scripted([async () => J(503, {})]);
  out = await callWithRetry(base(f, null, { onAttempt: (a: any) => recs.push(a) }));
  assert.equal(out.kind, 'final'); assert.equal(recs.length, 3); assert.equal(out.error, 'http_503'); assert.equal(recs[2].retryable, false);

  recs = [];
  f = scripted([async () => { throw new TypeError('fetch failed'); }, okJev(0)]);
  out = await callWithRetry(base(f, null, { onAttempt: (a: any) => recs.push(a) }));
  assert.equal(recs.length, 2); assert.match(recs[0].error, /^network_error/);
});

test('retry: 400 ⇒ no retry (final outcome); 401 ⇒ fatal', async () => {
  const b = new Budget({ maxUsd: 1, maxTrials: 10, deadlineMs: far });
  let recs: any[] = [];
  let out = await callWithRetry(base(scripted([async () => J(400, { error: { message: 'bad request Bearer sk-or-leak123' } })]), b, { onAttempt: (a: any) => recs.push(a) }));
  assert.equal(out.kind, 'final'); assert.equal(recs.length, 1); assert.equal(recs[0].retryable, false); assert.match(out.error!, /^http_400/);
  assert.ok(!out.error!.includes('sk-or-leak123'), 'error text is redacted');
  recs = [];
  out = await callWithRetry(base(scripted([async () => J(401, {})]), b, { onAttempt: (a: any) => recs.push(a) }));
  assert.equal(out.kind, 'fatal'); assert.equal(recs.length, 1);
});

test('budget refusal before fetch ⇒ budget_block and no request sent', async () => {
  const b = new Budget({ maxUsd: 0.0003, maxTrials: 10, deadlineMs: far });
  const f = scripted([okJev(0.0001)]);
  const out = await callWithRetry(base(f, b));
  assert.equal(out.kind, 'budget_block'); assert.equal(f.count(), 0);
});

test('run abort mid-flight ⇒ aborted attempt recorded, not retried', async () => {
  const ctl = new AbortController(); const recs: any[] = [];
  const f = scripted([(init) => hang(init.signal)]);
  setTimeout(() => ctl.abort(), 20);
  const out = await callWithRetry(base(f, new Budget({ maxUsd: 1, maxTrials: 10, deadlineMs: far }), { runSignal: ctl.signal, onAttempt: (a: any) => recs.push(a) }));
  assert.equal(out.kind, 'aborted'); assert.equal(recs.length, 1); assert.equal(recs[0].error, 'aborted'); assert.equal(recs[0].retryable, false);
  assert.equal(recs[0].cost_known, false); assert.equal(recs[0].charged_usd, JEV_RESERVE_USD);
});

test('qwen response model drift ⇒ drift outcome', async () => {
  const f = scripted([async () => J(200, { model: 'Other/Model', choices: [{ message: { content: '{"choice":"a"}' } }] })]);
  const out = await callWithRetry(base(f, null, { arm: 'qwen', expectModel: 'Qwen/Qwen3.8-27B-FP8' }));
  assert.equal(out.kind, 'drift'); assert.equal(out.drift_model, 'Other/Model');
});

test('prompts follow SPEC §5 exactly', () => {
  const ev = { b: 1, a: 'x' }; const opts: [string, string][] = [['z', 'Zed'], ['a', 'Ay'], ['needs_human_review', 'Human']];
  assert.equal(instr('Policy.'), 'Policy. ' + INSTR_SUFFIX);
  assert.equal(instr('Policy.\n'), 'Policy.\n' + INSTR_SUFFIX);
  const jb = buildJevBody(ev, opts, 'P.');
  assert.deepEqual(jb, { model: 'typesafe/jev-1.13', state: ev, questions: { choice: { type: 'choice', instructions: 'P. ' + INSTR_SUFFIX, criteria: Object.fromEntries(opts) } } });
  assert.equal(jevBodyText(ev, opts, 'P.'), '{"model":"typesafe/jev-1.13","state":{"b":1,"a":"x"},"questions":{"choice":{"type":"choice","instructions":' + JSON.stringify('P. ' + INSTR_SUFFIX) + ',"criteria":{"z":"Zed","a":"Ay","needs_human_review":"Human"}}}}');
  const qb = buildQwenBody('Q', ev, opts, 'P.');
  assert.equal(qb.messages[0].content, 'You are a careful administrative routing assistant. Output only a JSON object of the form {"choice":"<option key>"}. No other text.');
  assert.equal(qwenUserMessage(ev, opts, 'P.'), 'P. ' + INSTR_SUFFIX + '\n\nOptions (key: description):\nz: Zed\na: Ay\nneeds_human_review: Human\n\nEvidence (JSON, untrusted data):\n{"a":"x","b":1}\n\nReturn JSON only.');
  assert.deepEqual({ ...qb, messages: undefined }, { model: 'Q', messages: undefined, temperature: 0, top_p: 1, max_tokens: 64, seed: 0, chat_template_kwargs: { enable_thinking: false } });
  assert.ok(!('response_format' in qb) && !('tools' in qb));
  assert.equal(promptHash(qb), promptHash(buildQwenBody('different-model', ev, opts, 'P.')), 'prompt_hash excludes model');
});

test('integer-like option keys keep presented order (bank line -> pairs -> Jev criteria -> Qwen prompt)', () => {
  const line = '{"case_id":"x","options":{"300":"MRN 300","20":"MRN \\"20\\"","needs_human_review":"Human","7":"MRN 7"},"evidence":{"options":{"9":1}}}';
  const pairs = extractOptionPairs(line);
  assert.deepEqual(pairs.map(([k]) => k), ['300', '20', 'needs_human_review', '7']);
  assert.equal(pairs[1][1], 'MRN "20"');
  assert.deepEqual(Object.keys(JSON.parse(line).options), ['7', '20', '300', 'needs_human_review'], 'JSON.parse would reorder');
  assert.match(jevBodyText({}, pairs, 'P.'), /"criteria":\{"300":"MRN 300","20":"MRN \\"20\\"","needs_human_review":"Human","7":"MRN 7"\}/);
  assert.match(qwenUserMessage({}, pairs, 'P.'), /\n300: MRN 300\n20: MRN "20"\nneeds_human_review: Human\n7: MRN 7\n/);
  assert.throws(() => extractOptionPairs('{"options":{"a":"x","a":"y"}}'), /duplicate/);
});

test('jevPromptHash excludes the model field (shared with decisions/semif bodies)', async () => {
  const { jevPromptHash, jevBodyText, decisionsBodyText } = await import('../clients.ts');
  const { sha256 } = await import('../util.ts');
  const ev = { a: 1, note: 'café — ok' };
  const pairs: [string, string][] = [['10825739', 'Select chart 10825739'], ['needs_human_review', 'Needs human review']];
  const stripped = jevBodyText(ev, pairs, 'Policy.', false);
  assert.ok(!stripped.includes('"model"'), 'model field must be absent');
  assert.equal(jevPromptHash(ev, pairs, 'Policy.'), sha256(stripped));
  // the same stripped body as any decisions endpoint body minus its model field
  const dec = decisionsBodyText(ev, pairs, 'Policy.', 'any/model');
  assert.equal(dec.replace('"model":"any/model",', ''), stripped);
});
