// Environment doctor (npm run doctor). Default mode makes NO network calls.
// --network additionally checks reachability of https://openrouter.ai/api/v1/models (unauthenticated GET).
// --live additionally runs one real jev call and one real Qwen call (cost < $0.01); needs OPENROUTER_API_KEY.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { credential } from './util.ts';
import { JEV_URL, jevBodyText } from './clients.ts';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
type Row = { name: string; status: 'PASS' | 'WARN' | 'FAIL'; detail?: string };
const rows: Row[] = [];

function findOnPath(names: string[]): string | null {
  for (const d of (process.env.PATH ?? '').split(path.delimiter)) {
    if (!d) continue;
    for (const n of names) {
      const p = path.join(d, n);
      try { fs.accessSync(p, fs.constants.X_OK); return p; } catch { /* keep looking */ }
    }
  }
  return null;
}

function findPython(): { cmd: string; label: string } | null {
  if (process.platform === 'win32') {
    const py = findOnPath(['py.exe']);
    if (py) return { py: undefined as never, cmd: 'py -3', } as any;
  }
  return null;
}

async function main() {
  const nodeParts = process.versions.node.split('.').map(Number);
  const nodeOk = nodeOkCheck();
  function nodeOkCheck(): boolean {
    const [a, b] = [Number(process.versions.node.split('.')[0]), Number(process.versions.node.split('.')[1] ?? 0)];
    return a > 22 || (a === 22 && b >= 6);
  }
  void nodeOk; void nodeOkCheck;

  const say = (s: string) => process.stdout.write(s + '\n');
  say(`synthetic-ehr-decision-evals doctor (offline by default; --network adds a reachability check; --live makes one billable call per arm)`);
  say(`repo: ${ROOT}`);
  say(`node ${process.versions.node} on ${process.platform}-${process.arch}, timezone ${Intl.DateTimeFormat().resolvedOptions().timeZone}`);
  say('');

  rows.push({ name: `node >= 22.6`, status: nodeOkCheck() ? 'PASS' : 'FAIL', detail: process.versions.node });
  const pyName = process.platform === 'win32' ? 'py.exe' : 'python3';
  const py = findOnPath([pyNameForPlatform()]);
  rows.push({ name: `python (${pyNameForPlatform()})`, status: py ? 'PASS' : 'FAIL', detail: py ?? 'not found on PATH' });

  // SHA256SUMS
  try {
    const sumsFile = path.join(ROOT, 'SHA256SUMS');
    if (!fs.existsSync(sumsFile)) throw new Error('SHA256SUMS missing; run: npm run checksums');
    let n = 0, bad: string[] = [];
    for (const line of fs.readFileSync(sumsFile, 'utf8').split('\n')) {
      const m = /^([0-9a-f]{64})  (.+)$/.exec(line.trim());
      if (!m) continue;
      n++;
      const f = path.join(ROOT, ...m[2].split('/'));
      const got = (await import('node:crypto')).createHash('sha256').update(fs.readFileSync(f)).digest('hex');
      if (got !== m[1]) bad.push(m[2]);
    }
    rows.push({ name: 'SHA256SUMS verifies', status: n > 0 && bad.length === 0 ? 'PASS' : 'FAIL', detail: `${n} files` + (bad.length ? `, mismatched: ${bad.join(', ')}` : '') });
  } catch (e: any) {
    rows.push({ name: 'SHA256SUMS verifies', status: 'FAIL', detail: String(e?.message ?? e) });
  }

  // credential (show first 8 chars only)
  let credShown = '';
  try { const k = credential(); rows.push({ name: 'OPENROUTER_API_KEY present', status: 'PASS', detail: `${k.slice(0, 8)}... (value never logged)` }); }
  catch { rows.push({ name: 'OPENROUTER_API_KEY present', status: 'WARN', detail: 'not set; needed only for the jev arm (env var or repo .env)' }); }

  // proxy guidance
  const proxy = process.env.HTTPS_PROXY ?? process.env.https_proxy ?? process.env.HTTP_PROXY ?? process.env.http_proxy;
  if (proxy) {
    const ok = process.env.NODE_USE_ENV_PROXY === '1';
    rows.push({
      name: 'proxy', status: ok ? 'PASS' : 'WARN',
      detail: `${proxy} detected` + (ok ? ' (NODE_USE_ENV_PROXY=1 set)' : ': set NODE_USE_ENV_PROXY=1 so fetch() honors it') +
        (process.env.NODE_EXTRA_CA_CERTS ? '' : '; if the proxy re-signs TLS, also set NODE_EXTRA_CA_CERTS=<corporate root CA .pem>'),
    });
  } else {
    rows.push({ name: 'proxy', status: 'PASS', detail: 'no HTTP(S)_PROXY set' });
  }

  if (process.argv.includes('--network')) {
    try {
      const t0 = Date.now();
      const r = await fetch('https://openrouter.ai/api/v1/models');
      rows.push({ name: 'openrouter reachable (unauthenticated /models)', status: r.ok ? 'PASS' : 'FAIL', detail: `http ${r.status} in ${Date.now() - t0} ms` });
    } catch (e: any) {
      rows.push({ name: 'openrouter reachable (unauthenticated /models)', status: 'FAIL', detail: `${e?.message ?? e} — check NODE_USE_ENV_PROXY=1 / NODE_EXTRA_CA_CERTS` });
    }
  }

  if (process.argv.includes('--live')) {
    try {
      const key = credential();
      const evidence = { request: { patient_name: 'Casey Test', dob: '1980-01-01', mrn: '12345678' }, candidates: [{ chart_id: '12345678', name: 'Casey Test', dob: '1980-01-01' }] };
      const opts: [string, string][] = [['<candidate chart id>', 'Select chart 12345678'], ['needs_human_review', 'Needs human review']];
      const t0 = Date.now();
      const r = await fetch(JEV_URL, { method: 'POST', headers: { Authorization: `Bearer ${credential()}`, 'Content-Type': 'application/json' }, body: jevBodyText(evidence, opts, 'Pick the candidate chart that belongs to the requested patient.') });
      const b: any = await r.json().catch(() => null);
      rows.push({ name: 'jev live trial', status: r.ok ? 'PASS' : 'FAIL', detail: `http ${r.status}, cost ${b?.usage?.cost ?? 'unknown'}` });
      void t0;
      const qbase = process.env.QWEN_BASE_URL, qmodel = process.env.QWEN_MODEL;
      if (qbase && qmodel) {
        const body = buildQwenBody(qmodel, evidence, opts, 'Route the item.');
        const rq = await fetch(qbase.replace(/\/+$/, '') + '/chat/completions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        rows.push({ name: 'qwen live trial', status: rq.ok ? 'PASS' : 'FAIL', detail: `http ${rq.status}` });
      } else {
        rows.push({ name: 'qwen live trial', status: 'WARN', detail: 'QWEN_BASE_URL / QWEN_MODEL not set; skipped' });
      }
      void key;
    } catch (e: any) {
      rows.push({ name: 'live trials', status: 'FAIL', detail: String(e?.message ?? e) });
    }
  }

  for (const r of rows) say(`  [${r.status}] ${r.name}${r.detail ? ' — ' + r.detail : ''}`);
  const fails = rows.filter((r) => r.status === 'FAIL').length;
  const warns = rows.filter((r) => r.status === 'WARN').length;
  say(`doctor: ${rows.length - fails - warns} pass, ${warns} warn, ${fails} fail`);
  process.exit(fails === 0 ? 0 : 1);
}

function pyNameForPlatform(): string {
  return process.platform === 'win32' ? 'py -3' : 'python3';
}
function nodeOkCheck(): boolean {
  const [a, b] = process.versions.node.split('.').map(Number);
  return a > 22 || (a === 22 && b >= 6);
}

main().catch((e) => { process.stderr.write(`doctor: ${e?.stack ?? e}\n`); process.exit(1); });
