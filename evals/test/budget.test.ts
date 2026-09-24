import test from 'node:test';
import assert from 'node:assert/strict';
import { Budget } from '../budget.ts';
import { callWithRetry, JEV_RESERVE_USD } from '../clients.ts';
import { J } from './helpers.ts';

const far = Date.parse('2035-01-01T00:00:00Z');

test('settle: missing / non-numeric / negative / non-finite cost is charged at reservation, flagged unknown, never 0', () => {
  for (const bad of [undefined, null, '0.0001', 'abc', NaN, Infinity, -Infinity, -0.001, {}, [0.0001]]) {
    const b = new Budget({ maxUsd: 1, maxTrials: 10, deadlineMs: far });
    const r = b.reserveCall(JEV_RESERVE_USD)!;
    const s = b.settle(r.id, bad);
    assert.equal(s.cost_known, false, String(bad)); assert.equal(s.reported_cost_usd, null); assert.equal(s.charged_usd, JEV_RESERVE_USD);
    const snap = b.snapshot();
    assert.equal(snap.unknown_cost_charged_usd, JEV_RESERVE_USD); assert.equal(snap.spent_reported_usd, 0); assert.equal(snap.reserved_outstanding_usd, 0);
    assert.equal(snap.committed_usd, JEV_RESERVE_USD);
  }
  const b = new Budget({ maxUsd: 1, maxTrials: 10, deadlineMs: far });
  const r = b.reserveCall(JEV_RESERVE_USD)!;
  const s = b.settle(r.id, 0);   // a reported 0 is a known cost of 0 (distinct from unknown)
  assert.equal(s.cost_known, true); assert.equal(s.reported_cost_usd, 0);
  assert.throws(() => b.settle(r.id, 0), /already-settled/);
});

test('callWithRetry: 200 without usage.cost ⇒ attempt charged at reservation and cost_known=false', async () => {
  const b = new Budget({ maxUsd: 1, maxTrials: 10, deadlineMs: far });
  const recs: any[] = [];
  for (const usage of [undefined, {}, { cost: '0.0003' }, { cost: null }, { cost: -1 }]) {
    await callWithRetry({ arm: 'jev', url: 'x', headers: {}, body: {}, timeoutMs: 1000, runSignal: new AbortController().signal,
      fetch: async () => J(200, { answers: {}, usage }), budget: b, reserveUsd: JEV_RESERVE_USD, trial_id: 't', concurrency: 1, segment: 1, phase: 'p', onAttempt: (a) => recs.push(a) });
  }
  assert.equal(recs.length, 5);
  for (const a of recs) { assert.equal(a.cost_known, false); assert.equal(a.reported_cost_usd, null); assert.equal(a.charged_usd, JEV_RESERVE_USD); assert.equal(a.reserved_usd, JEV_RESERVE_USD); }
  assert.ok(Math.abs(b.snapshot().unknown_cost_charged_usd - 5 * JEV_RESERVE_USD) < 1e-12);
});

test('concurrency: 200 concurrent reservation attempts (with retries, mixed settle) never exceed maxUsd or maxTrials', async () => {
  for (let round = 0; round < 5; round++) {
    const maxUsd = 0.01;          // = 25 reservations of 0.0004
    const b = new Budget({ maxUsd, maxTrials: 37, deadlineMs: far });
    let peak = 0, peakTrials = 0, admittedOk = 0;
    const check = () => {
      const s = b.snapshot();
      peak = Math.max(peak, s.committed_usd); peakTrials = Math.max(peakTrials, s.trials_admitted);
      assert.ok(s.committed_usd <= maxUsd + 1e-12, `committed ${s.committed_usd} > ${maxUsd}`);
      assert.ok(s.trials_admitted <= 37);
    };
    const worker = async (i: number) => {
      await new Promise((r) => setTimeout(r, Math.random() * 5));
      if (!b.admitTrial('trial-' + i).ok) return;
      admittedOk++;
      for (let attempt = 0; attempt < 3; attempt++) {          // up to 2 retries per trial
        const r = b.reserveCall(0.0004); check();
        if (!r) return;
        await new Promise((res) => setTimeout(res, Math.random() * 5));
        const roll = Math.random();
        b.settle(r.id, roll < 0.4 ? Math.random() * 0.0004 : roll < 0.7 ? undefined : 'NaN'); check();
        if (Math.random() < 0.5) return;                           // success, no retry
      }
    };
    await Promise.all(Array.from({ length: 200 }, (_, i) => worker(i)));
    check();
    const s = b.snapshot();
    assert.equal(s.reserved_outstanding_usd, 0); assert.equal(s.open_reservations, 0);
    assert.ok(s.committed_usd <= maxUsd); assert.equal(admittedOk, 37); assert.equal(s.trials_admitted, 37);
    assert.ok(s.refused_reservations > 0, 'cap should have bound');
  }
});

test('admitTrial: re-admitting an already-counted trial is free; restore spans segments; deadline margin', () => {
  let now = Date.parse('2026-09-23T11:57:00Z');
  const b = new Budget({ maxUsd: 1, maxTrials: 2, deadlineMs: Date.parse('2026-09-23T12:00:00Z'), deadlineMarginMs: 120_000 }, () => now);
  b.restore({ reportedUsd: 0.5, unknownUsd: 0.0008, trialIds: ['a'] });
  assert.equal(b.admitTrial('a').ok, true); assert.equal(b.admitTrial('b').ok, true);
  assert.deepEqual(b.admitTrial('c'), { ok: false, reason: 'max_trials' });
  assert.ok(Math.abs(b.snapshot().committed_usd - 0.5008) < 1e-12);
  now = Date.parse('2026-09-23T11:58:30Z');
  assert.deepEqual(b.admitTrial('a'), { ok: false, reason: 'deadline' });
});
