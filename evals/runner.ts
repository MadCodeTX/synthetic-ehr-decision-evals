// Production runner for the synthetic EHR decision benchmark. See ../docs/SPEC.md and README.md.
// Usage: node --experimental-strip-types evals/runner.ts --run-dir <dir> --plan <plan.json>
//          --bank <bank.jsonl> --policy <policy.json> --phases pilot,broad --concurrency 4 [...]
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { Budget } from './budget.ts';
import { inputHash, extractOptionPairs } from './canon.ts';
import {
  jevBodyText, jevPromptHash, decisionsBodyText, semifBodyText, buildQwenBody, callWithRetry, promptHash, INSTR_SUFFIX, QWEN_SYSTEM, QWEN_PARAMS,
  JEV_MODEL, JEV_URL, JEV_RESERVE_USD, DECISIONS_MODEL, DECISIONS_EXPECTED, DECISIONS_COST_LABEL, SEMIF_MODEL, SEMIF_EXPECTED, SEMIF_COST_LABEL, DEFAULT_TIMEOUTS, RETRY_POLICY,
  type FetchLike, type AttemptRecord, type CallOutcome,
} from './clients.ts';
import { probeQwen, pinModel, isDrift, healthOk, probeDecisions, type QwenProbe, type DecisionsProbe } from './health.ts';
import { JsonlWriter, readJsonl, writeJsonAtomic, writeJsonOnce } from './store.ts';
import { makeMockFetch } from './mock.ts';
import { validateJev, validateQwen } from './validate.ts';
import { sha256, nowUtc, localIso, redactText, sleep, credential } from './util.ts';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const KNOWN_PHASES = ['pilot', 'broad', 'counterfactual', 'repeat', 'load_c1', 'load_c2', 'load_c4', 'load_c8', 'holdout'];
export const SPEC_MAX_USD = 5.0;
export const SPEC_MAX_TRIALS = 10_000;
export const QWEN_RESERVE_USD = 0.0004;
export const QWEN_COST_LABEL = 'openrouter.ai: usage.cost summed per attempt (unknown attempts charged at reservation); local/self-hosted: free';

export type RunOptions = {
  runDir: string; planPath: string; bankPath: string; policyPath: string;
  phases: string[]; concurrency: number; maxTrials?: number; maxUsd?: number; deadline?: string;
  qwenBase?: string; qwenModel?: string; expectQwenId?: string | null; decisionsBase?: string; semifBase?: string; dryRun?: boolean; limit?: number | null;
  // tuning / test hooks (not CLI-exposed except where noted)
  timeouts?: { jev_ms: number; qwen_ms: number; decisions_ms?: number; semif_ms?: number };
  healthIntervalMs?: number; statusIntervalMs?: number; stopPollMs?: number; backoffScale?: number;
  deadlineMarginMs?: number; maxConsecutiveTransportFailures?: number;
};
export type RunDeps = {
  fetch?: FetchLike; credential?: () => string; signal?: AbortSignal; log?: (s: string) => void;
  sleep?: (ms: number, s?: AbortSignal) => Promise<boolean>;
};
export type RunSummary = { exitCode: number; stopReason: string | null; segment: number; pairsWritten: number; resultsWritten: number; attemptsWritten: number; runDir: string };

type Case = { case_id: string; workflow: string; split: string; stratum: string; evidence: unknown; options: Record<string, string>; optionPairs: [string, string][]; expected: string; [k: string]: unknown };
type Arm = 'jev' | 'qwen' | 'decisions' | 'semif';
type Trial = { trial_id: string; case_id: string; phase: string; rep: number; arm_order: Arm[] };

export function phaseConcurrency(phase: string, requested: number): number {
  const m = /^load_c(\d+)$/.exec(phase);
  return m ? Number(m[1]) : requested;
}

function listTs(dir: string, base = dir): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listTs(p, base));
    else if (e.name.endsWith('.ts')) out.push(p);
  }
  return out.sort();
}
export function harnessHashes(): Record<string, string> {
  const h: Record<string, string> = {};
  for (const f of listTs(HERE)) h[f] = sha256(fs.readFileSync(f));
  return h;
}

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b); const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export function abstainKeyFor(c: Case, policyAbstain: string | undefined): string | null {
  if (policyAbstain && Object.hasOwn(c.options, policyAbstain)) return policyAbstain;
  if (Object.hasOwn(c.options, 'abstain')) return 'abstain';   // seed chart cases
  return null;
}

export async function run(opts: RunOptions, deps: RunDeps = {}): Promise<RunSummary> {
  const log = deps.log ?? ((s: string) => process.stderr.write(s + '\n'));
  const dryRun = !!opts.dryRun;
  const timeouts = opts.timeouts ? { ...DEFAULT_TIMEOUTS, ...opts.timeouts } : DEFAULT_TIMEOUTS;
  const maxUsd = opts.maxUsd ?? SPEC_MAX_USD;
  const maxTrials = opts.maxTrials ?? SPEC_MAX_TRIALS;
  if (!(maxUsd >= 0 && maxUsd <= SPEC_MAX_USD)) throw new Error(`--max-usd must be in [0, ${SPEC_MAX_USD}]`);
  if (!(Number.isInteger(maxTrials) && maxTrials >= 0 && maxTrials <= SPEC_MAX_TRIALS)) throw new Error(`--max-trials must be an integer in [0, ${SPEC_MAX_TRIALS}]`);
  if (!(Number.isInteger(opts.concurrency) && opts.concurrency >= 1)) throw new Error('--concurrency must be an integer >= 1');
  const deadlineStr = opts.deadline ?? new Date(Date.now() + 24 * 3600_000).toISOString(); // default: now + 24 h
  const deadlineMs = Date.parse(deadlineStr);
  if (!Number.isFinite(deadlineMs)) throw new Error(`bad --deadline ${deadlineStr}`);
  const deadlineMarginMs = opts.deadlineMarginMs ?? (timeouts.jev_ms + timeouts.qwen_ms);
  const phases = opts.phases;
  if (!phases.length) throw new Error('--phases is required');
  for (const p of phases) if (!KNOWN_PHASES.includes(p) && !/^load_c\d+$/.test(p)) throw new Error(`unknown phase ${p}`);
  if (new Set(phases).size !== phases.length) throw new Error('duplicate phase in --phases');

  // ---------- inputs + hashes ----------
  const policyBytes = fs.readFileSync(opts.policyPath), bankBytes = fs.readFileSync(opts.bankPath), planBytes = fs.readFileSync(opts.planPath);
  const hashes = { policy_hash: sha256(policyBytes), fixture_hash: sha256(bankBytes), plan_hash: sha256(planBytes) };
  const policy = JSON.parse(policyBytes.toString('utf8'));
  const cases = new Map<string, Case>();
  for (const line of bankBytes.toString('utf8').split('\n')) {
    if (!line.trim()) continue;
    const c = JSON.parse(line) as Case;
    c.optionPairs = extractOptionPairs(line);   // presented order, robust to integer-like keys
    if (cases.has(c.case_id)) throw new Error(`duplicate case_id ${c.case_id}`);
    cases.set(c.case_id, c);
  }
  const plan = JSON.parse(planBytes.toString('utf8'));
  const allTrials: Trial[] = plan.trials;
  const seenTrial = new Set<string>();
  for (const t of allTrials) { if (seenTrial.has(t.trial_id)) throw new Error(`duplicate trial_id ${t.trial_id}`); seenTrial.add(t.trial_id); }
  const selected = new Map<string, Trial[]>();
  for (const p of phases) selected.set(p, allTrials.filter((t) => t.phase === p));
  for (const [p, ts] of selected) for (const t of ts) {
    const c = cases.get(t.case_id);
    if (!c) throw new Error(`trial ${t.trial_id}: unknown case ${t.case_id}`);
    const wf = policy?.workflows?.[c.workflow];
    if (!wf || typeof wf.policy_text !== 'string') throw new Error(`case ${c.case_id}: workflow ${c.workflow} missing from policy`);
    if (!c.options || !Object.keys(c.options).length) throw new Error(`case ${c.case_id}: no options`);
    const pk = c.optionPairs.map(([k]) => k);
    if (pk.length !== Object.keys(c.options).length || pk.some((k) => c.options[k] !== c.optionPairs.find(([kk]) => kk === k)![1]))
      throw new Error(`case ${c.case_id}: ordered option extraction disagrees with parsed options`);
    if (!Object.hasOwn(c.options, c.expected) && (c as any).ambiguity?.kind !== 'no_valid_option')
      throw new Error(`case ${c.case_id}: expected ${c.expected} is not an option key (and ambiguity is not no_valid_option)`);
    const ao = t.arm_order;
    const isPaired = Array.isArray(ao) && ao.length === 2 && ao.includes('jev') && ao.includes('qwen');
    const isJevOnly = Array.isArray(ao) && ao.length === 1 && ao[0] === 'jev';
    const isQwenOnly = Array.isArray(ao) && ao.length === 1 && ao[0] === 'qwen';
    const isDecisionsOnly = Array.isArray(ao) && ao.length === 1 && (ao[0] === 'decisions' || ao[0] === 'laya');
    const isSemifOnly = Array.isArray(ao) && ao.length === 1 && ao[0] === 'semif';
    if (!isPaired && !isJevOnly && !isQwenOnly && !isDecisionsOnly && !isSemifOnly) throw new Error(`trial ${t.trial_id}: bad arm_order`);
    if (isDecisionsOnly) t.arm_order = ['decisions'];  // canonical id for the generic decisions arm (legacy plans may say 'laya')
    if (p !== t.phase) throw new Error('phase mismatch');
  }
  // Which arms this launch will actually execute (from the selected phases).
  const armsInPlan = new Set<Arm>();
  for (const ts of selected.values()) for (const t of ts) for (const a of t.arm_order) armsInPlan.add(a);
  const usesJev = armsInPlan.has('jev'), usesQwen = armsInPlan.has('qwen'), usesDecisions = armsInPlan.has('decisions') || armsInPlan.has('laya'), usesSemif = armsInPlan.has('semif');
  if (!armsInPlan.size) throw new Error('plan contains no runnable arms for the selected phases');

  // ---------- endpoints (no defaults: each arm must be given its base URL) ----------
  const qwenBase = (opts.qwenBase ?? process.env.QWEN_BASE_URL ?? '').replace(/\/+$/, '');
  const qwenModel = opts.qwenModel ?? process.env.QWEN_MODEL ?? null;
  if (usesQwen && !qwenBase) throw new Error('qwen arm in the plan but no endpoint: pass --qwen-base (or set QWEN_BASE_URL), e.g. an OpenAI-compatible /v1 base URL');
  const isQwenOpenRouter = !!qwenBase && /(^|\.)openrouter\.ai$/.test(new URL(qwenBase).hostname);
  if (isQwenOpenRouter && !qwenModel) throw new Error('qwen on openrouter.ai requires --qwen-model (or QWEN_MODEL): OpenRouter hosts many models, so there is no single served id to pin');
  const decisionsBase = (opts.decisionsBase ?? process.env.DECISIONS_BASE_URL ?? '').replace(/\/+$/, '');
  const decisionsEndpoint = decisionsBase + '/v1/decisions';
  if (usesDecisions && !decisionsBase) throw new Error('decisions arm in the plan but no endpoint: pass --decisions-base (or set DECISIONS_BASE_URL), e.g. http://host:8090');
  const semifBase = (opts.semifBase ?? process.env.SEMIF_BASE_URL ?? '').replace(/\/+$/, '');
  const semifEndpoint = semifBase + '/v1/decisions';
  if (usesSemif && !semifBase) throw new Error('semif arm in the plan but no endpoint: pass --semif-base (or set SEMIF_BASE_URL)');
  const expectQwenId = opts.expectQwenId ?? (isQwenOpenRouter ? qwenModel : (!qwenModel && dryRun ? 'Qwen/Qwen3.8-27B-FP8' : qwenModel));

  // ---------- run dir / resume ----------
  fs.mkdirSync(opts.runDir, { recursive: true });
  const F = (n: string) => path.join(opts.runDir, n);
  const resume = fs.existsSync(F('manifest.json'));
  let manifest: any = null;
  const hHashes = harnessHashes();
  if (resume) {
    manifest = JSON.parse(fs.readFileSync(F('manifest.json'), 'utf8'));
    for (const k of ['policy_hash', 'fixture_hash', 'plan_hash'] as const)
      if (manifest.hashes?.[k] !== hashes[k]) throw new Error(`resume refused: ${k} differs from manifest (${manifest.hashes?.[k]} != ${hashes[k]})`);
    if (!!manifest.dry_run !== dryRun) throw new Error('resume refused: dry-run flag differs from manifest');
    if (usesQwen && manifest.arms?.qwen?.base_url !== qwenBase) throw new Error('resume refused: qwen base differs from manifest');
    if (usesDecisions && manifest.arms?.decisions?.base_url !== decisionsBase) throw new Error('resume refused: decisions base differs from manifest');
    if (usesSemif && manifest.arms?.semif?.base_url !== semifBase) throw new Error('resume refused: semif base differs from manifest');
  }
  const priorSegments = readJsonl(F('segments.jsonl')).rows;
  const segment = priorSegments.reduce((m: number, r: any) => Math.max(m, r.segment ?? 0), 0) + 1;

  // ---------- credentials (value never logged) ----------
  const fetchImpl: FetchLike = dryRun ? makeMockFetch({ qwenModel: expectQwenId ?? 'Qwen/Qwen3.8-27B-FP8', semifModel: () => SEMIF_EXPECTED[0] ?? 'semif-mock' }) : (deps.fetch ?? (globalThis.fetch as FetchLike));
  const qwenBilled = usesQwen && isQwenOpenRouter && !dryRun; // OpenRouter reports usage.cost; self-hosted qwen is free
  const needsKey = usesJev || qwenBilled;
  const apiKey = dryRun ? 'dry-run-no-key' : !needsKey ? 'unused (no billed arm in plan)' : (deps.credential ?? (() => credential()))();
  const jevHeaders = { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' };
  const qwenHeaders = isQwenOpenRouter ? { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' };
  const decisionsHeaders = { 'Content-Type': 'application/json' }; // self-hosted; no credential
  const semifHeaders = { 'Content-Type': 'application/json' }; // self-hosted; no credential

  // ---------- startup health + pin (before manifest, before any model call) ----------
  const runCtl = new AbortController();
  // Qwen on openrouter.ai has no /health or /models endpoints to pin against; the response
  // `model` field of every call is recorded and drift-checked instead.
  const probeQ = usesQwen && !isQwenOpenRouter ? await probeQwen(qwenBase, fetchImpl, runCtl.signal) : null;
  let pinned: string = 'n/a (qwen arm not in plan)';
  if (usesQwen) {
    if (isQwenOpenRouter) pinned = qwenModel!;
    else {
      const pin = pinModel(probeQ!, expectQwenId);
      if (!pin.ok) throw new Error(`qwen startup check failed: ${pin.reason}`);
      pinned = pin.pinned;
    }
  }
  const decisionsProbe: DecisionsProbe | null = usesDecisions ? await probeDecisions(decisionsBase, fetchImpl, runCtl.signal) : null;
  if (usesDecisions && !decisionsProbe!.ok)
    throw new Error(`decisions startup check failed: ${decisionsProbe!.health.error ?? 'health body not ok'} (${decisionsProbe!.health.url})`);
  // The SemIf service exposes the same /health contract as the decisions service.
  const semifProbe: DecisionsProbe | null = usesSemif ? await probeDecisions(semifBase, fetchImpl, runCtl.signal) : null;
  if (usesSemif && !semifProbe!.ok)
    throw new Error(`semif startup check failed: ${semifProbe!.health.error ?? 'health body not ok'} (${semifProbe!.health.url})`);
  if (resume && usesQwen && manifest.arms?.qwen?.pinned_model !== pinned) throw new Error(`resume refused: served qwen model ${pinned} != manifest pin ${manifest.arms?.qwen?.pinned_model}`);

  // ---------- writers ----------
  const W = {
    attempts: new JsonlWriter(F('attempts.jsonl')), results: new JsonlWriter(F('results.jsonl')), pairs: new JsonlWriter(F('pairs.jsonl')),
    events: new JsonlWriter(F('events.jsonl')), segments: new JsonlWriter(F('segments.jsonl'), 0),
  };
  const event = (type: string, data: Record<string, unknown> = {}) => W.events.write({ type, t_utc: nowUtc(), t_local: localIso(), segment, ...data });

  const startMs = Date.now();
  const concurrencyPlan = Object.fromEntries(phases.map((p) => [p, phaseConcurrency(p, opts.concurrency)]));
  const caps = { max_usd: maxUsd, max_trials: maxTrials, deadline_utc: new Date(deadlineMs).toISOString(), deadline_local: localIso(deadlineMs), dispatch_margin_ms: deadlineMarginMs, jev_reserve_usd_per_attempt: JEV_RESERVE_USD };
  const healthEvidence = {
    qwen: usesQwen && !isQwenOpenRouter
      ? { t_utc: probeQ!.t_utc, health_url: probeQ!.health.url, health_status: probeQ!.health.status, health_error: probeQ!.health.error, models_url: probeQ!.models.url, models_status: probeQ!.models.status, models_data_subset: probeQ!.models.data_subset }
      : usesQwen ? { t_utc: nowUtc(), note: 'openrouter.ai: no /health or /models probe; per-call response model is drift-checked', model: qwenModel } : null,
    jev: usesJev ? 'not pre-probed (a probe would be a billable call)' : 'arm not in plan',
    decisions: decisionsProbe
      ? { t_utc: decisionsProbe.t_utc, health_url: decisionsProbe.health.url, health_status: decisionsProbe.health.status, health_error: decisionsProbe.health.error, ok: decisionsProbe.ok, loaded: decisionsProbe.loaded, cfg: decisionsProbe.cfg }
      : null,
    semif: semifProbe
      ? { t_utc: semifProbe.t_utc, health_url: semifProbe.health.url, health_status: semifProbe.health.status, health_error: semifProbe.health.error, ok: semifProbe.ok, loaded: semifProbe.loaded, cfg: semifProbe.cfg }
      : null,
  };
  if (!resume) {
    manifest = {
      manifest_version: 'v1', run_id: path.basename(path.resolve(opts.runDir)), dry_run: dryRun,
      created_utc: nowUtc(startMs), created_local: localIso(startMs),
      deadline_utc: caps.deadline_utc, resolved_deadline: { utc: caps.deadline_utc, local: caps.deadline_local },
      host: os.hostname(), node_version: process.version, platform: `${process.platform}-${process.arch}`,
      harness_files: hHashes, hashes,
      inputs: { policy: path.resolve(opts.policyPath), bank: path.resolve(opts.bankPath), plan: path.resolve(opts.planPath), policy_version: policy.policy_version ?? null, plan_version: plan.plan_version ?? null, order_seed: plan.order_seed ?? null, n_cases: cases.size, n_plan_trials: allTrials.length },
      arms: {
        ...(usesJev ? { jev: { model: JEV_MODEL, endpoint: dryRun ? 'mock:' + JEV_URL : JEV_URL, credential_source: dryRun ? 'none (dry-run)' : 'OPENROUTER_API_KEY env var or repo .env (value never recorded)', request_template: { model: JEV_MODEL, state: '<evidence>', questions: { choice: { type: 'choice', instructions: '<INSTR(wf)>', criteria: '<options, presented order>' } } } } } : {}),
        ...(usesQwen ? { qwen: { base_url: qwenBase, endpoint: (dryRun ? 'mock:' : '') + qwenBase + '/chat/completions', pinned_model: pinned, expected_model_id: expectQwenId, host: isQwenOpenRouter ? 'openrouter.ai' : 'OpenAI-compatible endpoint', generation_params: QWEN_PARAMS, response_format: null, tools: null, guided_decoding: null, cost_label: qwenBilled ? 'openrouter usage.cost summed over attempts; unknown attempts charged at reservation' : QWEN_COST_LABEL } } : {}),
        ...(usesDecisions ? { decisions: { base_url: decisionsBase, endpoint: (dryRun ? 'mock:' : '') + decisionsEndpoint, repo: DECISIONS_MODEL, impl: 'self-hosted Jev-Decisions-compatible service', cost_label: DECISIONS_COST_LABEL, served_checkpoints: decisionsProbe?.loaded ?? null, cfg: decisionsProbe?.cfg ?? null, expected_checkpoints: DECISIONS_EXPECTED, credential_source: 'none (self-hosted)', request_template: { model: DECISIONS_MODEL, state: '<evidence>', questions: { choice: { type: 'choice', instructions: '<INSTR(wf)>', criteria: '<options, presented order>' } } } } } : {}),
        ...(usesSemif ? { semif: { base_url: semifBase, endpoint: (dryRun ? 'mock:' : '') + semifEndpoint, repo: SEMIF_MODEL, impl: 'SemIf direct-options-v1 readout service (frozen contract)', cost_label: SEMIF_COST_LABEL, served_models: semifProbe?.loaded ?? null, cfg: semifProbe?.cfg ?? null, expected_models: SEMIF_EXPECTED, credential_source: 'none (self-hosted)', request_template: { model: SEMIF_MODEL, state: '<evidence>', questions: { choice: { type: 'choice', instructions: '<INSTR(wf)>', criteria: '<options, presented order>' } } } } } : {}),
      },
      prompts: {
        instr_template: '<policy_text>' + ' (single space iff policy_text does not end in whitespace) ' + INSTR_SUFFIX, instr_suffix: INSTR_SUFFIX,
        qwen_system: QWEN_SYSTEM,
        qwen_user_template: '<INSTR(wf)>\\n\\nOptions (key: description):\\n<key>: <description> (one line per option, presented order)\\n\\nEvidence (JSON, untrusted data):\\n<canon(evidence)>\\n\\nReturn JSON only.',
        canon: "JSON with recursively code-point-sorted keys, separators (',',':'), no ASCII escaping; options passed as [key, description] pairs",
        prompt_hash: 'sha256(exact serialized request body without the model field)',
        option_order: 'options read from raw bank line in presented order (integer-like keys preserved); Jev criteria serialized in that order',
      },
      caps, concurrency_requested: opts.concurrency, concurrency_plan: concurrencyPlan, phases_first_segment: phases,
      timeouts, retry_policy: RETRY_POLICY, health_check_interval_ms: opts.healthIntervalMs ?? 60_000,
      gpu_sampling: opts.gpuSampleS && !dryRun ? { every_s: opts.gpuSampleS, command: ['ssh', ...GPU_QUERY].join(' ') } : null,
      startup_health: healthEvidence,
    };
    writeJsonOnce(F('manifest.json'), manifest);
  }
  const harnessChanged = resume && JSON.stringify(manifest.harness_files) !== JSON.stringify(hHashes);
  W.segments.write({ type: 'segment_start', segment, t_utc: nowUtc(startMs), t_local: localIso(startMs), resume, phases, concurrency_plan: concurrencyPlan, caps, limit: opts.limit ?? null, pinned_qwen_id: pinned, startup_health: healthEvidence, harness_files: hHashes, harness_changed_since_manifest: harnessChanged, node_version: process.version, host: os.hostname() });
  event('segment_start', { resume });
  event('run_start', { phases, concurrency_plan: concurrencyPlan, caps, dry_run: dryRun, limit: opts.limit ?? null });
  if (harnessChanged) event('error', { error: 'harness_changed_since_manifest', note: 'harness .ts hashes differ from manifest; recorded in segments.jsonl' });
  event('health_check', { startup: true, ok: usesQwen && !isQwenOpenRouter ? healthOk(probeQ!) : (semifProbe?.ok ?? decisionsProbe?.ok ?? true), health_status: (usesQwen && !isQwenOpenRouter ? probeQ!.health.status : semifProbe?.health.status ?? decisionsProbe?.health.status) ?? null, models_ids: usesQwen && !isQwenOpenRouter ? probeQ!.models.ids : [], decisions_ok: decisionsProbe?.ok ?? null, semif_ok: semifProbe?.ok ?? null, error: (usesQwen && !isQwenOpenRouter ? (probeQ!.health.error ?? probeQ!.models.error) : semifProbe?.health.error ?? decisionsProbe?.health.error) ?? null });
  if (usesSemif) event('semif_checkpoints', { served: semifProbe!.loaded, expected_set: SEMIF_EXPECTED, cfg: semifProbe!.cfg });
  if (usesQwen) event('model_pin', { arm: 'qwen', pinned_model_id: pinned, expected: expectQwenId, probed: !isQwenOpenRouter });
  if (usesDecisions) event('decisions_checkpoints', { served: decisionsProbe!.loaded, expected_set: DECISIONS_EXPECTED, cfg: decisionsProbe!.cfg });

  // ---------- budget restore ----------
  const budget = new Budget({ maxUsd, maxTrials, deadlineMs, deadlineMarginMs });
  const priorAttempts = readJsonl<AttemptRecord>(F('attempts.jsonl'));
  let priorReported = 0, priorUnknown = 0;
  const priorTrialIds = new Set<string>();
  for (const a of priorAttempts.rows) {
    priorTrialIds.add(a.trial_id);
    if (a.arm !== 'jev') continue;
    if (a.cost_known && typeof a.reported_cost_usd === 'number') priorReported += a.reported_cost_usd;
    else priorUnknown += typeof a.reserved_usd === 'number' ? a.reserved_usd : JEV_RESERVE_USD;
  }
  budget.restore({ reportedUsd: priorReported, unknownUsd: priorUnknown, trialIds: priorTrialIds });
  const priorPairs = readJsonl(F('pairs.jsonl')).rows;
  const completed = new Set<string>(priorPairs.map((p: any) => p.trial_id));
  if (resume) event('resume', { prior_attempts: priorAttempts.rows.length, prior_attempt_lines_unparseable: priorAttempts.bad, prior_pairs: completed.size, restored_reported_usd: priorReported, restored_unknown_usd: priorUnknown, restored_trials_admitted: priorTrialIds.size });

  // ---------- counters for status ----------
  const pairsByPhase: Record<string, number> = {};
  for (const p of priorPairs as any[]) pairsByPhase[p.phase] = (pairsByPhase[p.phase] ?? 0) + 1;
  const st = {
    attempts: 0, attempt_errors: 0, retries: 0, results: 0, pairs: 0,
    invalid: { jev: 0, qwen: 0, decisions: 0, semif: 0 } as Record<string, number>, valid: { jev: 0, qwen: 0, decisions: 0, semif: 0 } as Record<string, number>,
    lat: { jev: [] as number[], qwen: [] as number[], decisions: [] as number[], semif: [] as number[] } as Record<string, number[]>,
    lastHealth: { t_utc: (usesQwen && !isQwenOpenRouter ? probeQ!.t_utc : semifProbe?.t_utc ?? decisionsProbe?.t_utc) ?? nowUtc(), ok: usesQwen && !isQwenOpenRouter ? healthOk(probeQ!) : (semifProbe?.ok ?? decisionsProbe?.ok ?? true), models_ids: usesQwen && !isQwenOpenRouter ? probeQ!.models.ids : [], ...(decisionsProbe ? { decisions: { t_utc: decisionsProbe.t_utc, ok: decisionsProbe.ok, health_status: decisionsProbe.health.status, error: decisionsProbe.health.error, loaded: decisionsProbe.loaded } } : {}), ...(semifProbe ? { semif: { t_utc: semifProbe.t_utc, ok: semifProbe.ok, health_status: semifProbe.health.status, error: semifProbe.health.error, loaded: semifProbe.loaded } } : {}) } as any,
    phase: null as string | null, concurrency: null as number | null, inflight: 0,
  };
  const totalSelected = [...selected.values()].reduce((a, ts) => a + ts.length, 0);

  // ---------- stop machinery ----------
  // Hard stop: no new attempts at all, in-flight fetches aborted (STOP file, signal, drift, fatal, hard deadline).
  // Drain: no new trial admissions; already-admitted trials finish (USD/trial caps, --limit, deadline margin).
  let stopReason: string | null = null; let drainReason: string | null = null; let signalled = false;
  const requestStop = (reason: string, data: Record<string, unknown> = {}) => {
    if (stopReason) return;
    stopReason = reason; event('stop_requested', { reason, ...data }); log(`[runner] stop requested: ${reason} (aborting in-flight)`);
    budget.stop(reason);
    runCtl.abort(new Error('run stopped: ' + reason));
  };
  const drain = (reason: string, eventType: string, data: Record<string, unknown> = {}) => {
    if (drainReason || stopReason) return;
    drainReason = reason; event(eventType, { reason, ...data }); log(`[runner] draining: ${reason}`);
    budget.stop(reason);
  };
  const onExternal = () => { signalled = true; event('signal', { reason: String(deps.signal?.reason ?? 'abort') }); requestStop('signal'); };
  if (deps.signal) { if (deps.signal.aborted) onExternal(); else deps.signal.addEventListener('abort', onExternal, { once: true }); }
  const stopPoll = setInterval(() => {
    if (fs.existsSync(F('STOP'))) requestStop('STOP file');
    if (Date.now() >= deadlineMs) { if (!stopReason) event('deadline', { deadline_utc: caps.deadline_utc }); requestStop('deadline (hard)'); }
    else if (budget.pastDispatchDeadline()) drain('deadline (dispatch margin)', 'deadline_drain', { deadline_utc: caps.deadline_utc, margin_ms: deadlineMarginMs });
  }, opts.stopPollMs ?? 500);
  if (fs.existsSync(F('STOP'))) requestStop('STOP file present at start');

  const writeStatus = (final = false) => {
    const elapsedMin = (Date.now() - startMs) / 60000;
    const pairRate = elapsedMin > 0 ? st.pairs / elapsedMin : 0;
    const remaining = [...selected.values()].flat().filter((t) => !completed.has(t.trial_id)).length;
    writeJsonAtomic(F('status.json'), {
      t_utc: nowUtc(), t_local: localIso(), final, segment, dry_run: dryRun, phase: st.phase, concurrency: st.concurrency, inflight_trials: st.inflight,
      stop_reason: stopReason, drain_reason: drainReason, completed_pairs_by_phase: pairsByPhase, completed_pairs_total: completed.size,
      this_segment: { attempts: st.attempts, attempt_errors: st.attempt_errors, retries: st.retries, results: st.results, pairs: st.pairs, invalid: st.invalid, valid: st.valid, elapsed_min: +elapsedMin.toFixed(3) },
      spend: budget.snapshot(), trials_admitted: budget.admitted.size,
      p50_latency_ms_last200: { jev: median(st.lat.jev), qwen: median(st.lat.qwen), decisions: median(st.lat.decisions) },
      valid_decisions_per_min: elapsedMin > 0 ? +((st.valid.jev + st.valid.qwen + st.valid.decisions) / elapsedMin).toFixed(3) : null,
      last_health: st.lastHealth,
      selected_trials: totalSelected, remaining_selected_trials: remaining,
      eta_utc: pairRate > 0 && !final ? nowUtc(Date.now() + (remaining / pairRate) * 60000) : null,
    });
  };
  writeStatus();
  const statusTimer = setInterval(() => { try { writeStatus(); } catch (e) { event('error', { error: 'status_write: ' + redactText((e as any)?.message) }); } }, opts.statusIntervalMs ?? 30_000);

  let healthBusy = false;
  const healthTimer = setInterval(async () => {
    if (healthBusy || runCtl.signal.aborted) return; healthBusy = true;
    try {
      if (usesQwen && !isQwenOpenRouter) {
        const p: QwenProbe = await probeQwen(qwenBase, fetchImpl, runCtl.signal);
        if (runCtl.signal.aborted) return;
        st.lastHealth = { ...st.lastHealth, qwen_model: pinned, ok: healthOk(p), models_ids: p.models.ids, health_status: p.health.status, error: p.health.error ?? p.models.error, t_utc: p.t_utc };
        event('health_check', st.lastHealth);
        if (isDrift(p, pinned)) { event('drift', { source: 'models_endpoint', pinned, served: p.models.ids }); requestStop('model drift (models endpoint)'); }
      }
      if (usesDecisions) {
        const dp: DecisionsProbe = await probeDecisions(decisionsBase, fetchImpl, runCtl.signal);
        if (runCtl.signal.aborted) return;
        st.lastHealth = { ...st.lastHealth, decisions: { t_utc: dp.t_utc, ok: dp.ok, health_status: dp.health.status, error: dp.health.error, loaded: dp.loaded } };
        event('health_check', st.lastHealth);
      }
      if (usesSemif) {
        const sp: DecisionsProbe = await probeDecisions(semifBase, fetchImpl, runCtl.signal);
        if (runCtl.signal.aborted) return;
        st.lastHealth = { ...st.lastHealth, semif: { t_utc: sp.t_utc, ok: sp.ok, health_status: sp.health.status, error: sp.health.error, loaded: sp.loaded } };
        event('health_check', st.lastHealth);
      }
    } finally { healthBusy = false; }
  }, opts.healthIntervalMs ?? 60_000);

  // ---------- trial execution ----------
  const consecutiveTransport: Record<Arm, number> = { jev: 0, qwen: 0, decisions: 0, semif: 0 };
  const maxConsec = opts.maxConsecutiveTransportFailures ?? 8;
  const onAttempt = (a: AttemptRecord) => {
    W.attempts.write(a); st.attempts++;
    if (a.error) st.attempt_errors++;
    if (a.attempt_no > 1) st.retries++;
  };

  async function runArm(arm: Arm, t: Trial, c: Case, conc: number): Promise<{ outcome: CallOutcome; result: any | null }> {
    const policyText: string = policy.workflows[c.workflow].policy_text;
    const body = arm === 'qwen' ? buildQwenBody(pinned, c.evidence, c.optionPairs, policyText) : null;
    const bodyText = arm === 'qwen' ? JSON.stringify(body) : arm === 'decisions' ? decisionsBodyText(c.evidence, c.optionPairs, policyText) : arm === 'semif' ? semifBodyText(c.evidence, c.optionPairs, policyText) : jevBodyText(c.evidence, c.optionPairs, policyText);
    const billed = arm === 'jev' || (arm === 'qwen' && qwenBilled);
    const outcome = await callWithRetry({
      arm, url: arm === 'jev' ? JEV_URL : arm === 'decisions' ? decisionsEndpoint : arm === 'semif' ? semifEndpoint : qwenBase + '/chat/completions', headers: arm === 'jev' ? jevHeaders : arm === 'decisions' ? decisionsHeaders : arm === 'semif' ? semifHeaders : qwenHeaders, body, bodyText,
      timeoutMs: arm === 'qwen' ? timeouts.qwen_ms : arm === 'decisions' ? timeouts.decisions_ms : arm === 'semif' ? (timeouts as any).semif_ms ?? timeouts.decisions_ms : timeouts.jev_ms, runSignal: runCtl.signal, fetch: fetchImpl,
      budget: billed ? budget : null, reserveUsd: billed ? (arm === 'jev' ? JEV_RESERVE_USD : QWEN_RESERVE_USD) : 0,
      trial_id: t.trial_id, concurrency: conc, segment, phase: t.phase, onAttempt,
      expectModel: arm === 'qwen' ? pinned : undefined,
      expectModelSet: arm === 'decisions' ? [...DECISIONS_EXPECTED] : arm === 'semif' ? [...SEMIF_EXPECTED] : undefined,
      backoffScale: opts.backoffScale ?? (dryRun ? 0.01 : 1), sleep: deps.sleep,
    });
    if (outcome.kind !== 'final') return { outcome, result: null };
    const keys = c.optionPairs.map(([k]) => k);
    const b = outcome.body;
    const ok2xx = outcome.http_status !== null && outcome.http_status >= 200 && outcome.http_status < 300 && !outcome.error;
    let choice: string | null = null, lenient: string | null = null, valid = false, failure: string | null = null;
    let probabilities: any = null, confidence: any = null, chosenP: any = null, raw: any = null;
    if (arm === 'jev' || arm === 'decisions' || arm === 'semif') {
      if (b && typeof b === 'object') raw = { id: b.id ?? null, model: b.model ?? null, answers: b.answers ?? null, usage: b.usage ?? null, ...(arm === 'decisions' ? { routing: b.routing ?? null, service: b.service ?? null } : arm === 'semif' ? { service: b.service ?? null } : {}), ...(b.error ? { error: redactText(b.error?.message ?? b.error, 300) } : {}) };
      if (ok2xx) {
        const v = validateJev(b, keys);
        valid = v.valid; choice = v.choice; failure = v.failure_reason; probabilities = v.probabilities; confidence = v.confidence; chosenP = v.chosen_probability;
      } else failure = outcome.error ?? 'no_response';
    } else {
      const content = b?.choices?.[0]?.message?.content;
      if (b && typeof b === 'object') raw = { id: b.id ?? null, model: b.model ?? null, content: content ?? null, finish_reason: b?.choices?.[0]?.finish_reason ?? null, usage: b.usage ?? null, ...(b.error ? { error: redactText(b.error?.message ?? b.error, 300) } : {}) };
      if (ok2xx) {
        const v = validateQwen(content, keys);
        valid = v.valid; choice = v.choice; lenient = v.lenient_choice; failure = v.failure_reason;
      } else failure = outcome.error ?? 'no_response';
    }
    const transportFail = !ok2xx && (outcome.error === 'timeout' || /^network_error/.test(outcome.error ?? '') || outcome.http_status === 429 || (outcome.http_status ?? 0) >= 500);
    consecutiveTransport[arm] = transportFail ? consecutiveTransport[arm] + 1 : 0;
    const usage = b?.usage ?? {};
    const num = (x: unknown) => (typeof x === 'number' && Number.isFinite(x) ? x : null);
    const promptTok = num(usage.prompt_tokens), complTok = num(usage.completion_tokens);
    const lastLat = outcome.latency_ms_final;
    const atts = outcome.attempts;
    const known = billed && atts.every((a) => a.cost_known);
    const reported = billed && atts.some((a) => a.cost_known) ? atts.reduce((s, a) => s + (a.reported_cost_usd ?? 0), 0) : null;
    const abstainKey = abstainKeyFor(c, policy.workflows[c.workflow].abstain_key);
    const result = {
      result_id: `${t.trial_id}|${arm}|s${segment}`, trial_id: t.trial_id, case_id: c.case_id, phase: t.phase, rep: t.rep, arm, segment,
      t_start_utc: nowUtc(outcome.t_start_ms), t_end_utc: nowUtc(outcome.t_end_ms), t_start_local: localIso(outcome.t_start_ms),
      workflow: c.workflow, stratum: c.stratum, split: c.split,
      input_hash: inputHash(c.workflow, c.evidence, c.optionPairs, policyText),
      prompt_hash: arm === 'qwen' ? promptHash(body!) : jevPromptHash(c.evidence, c.optionPairs, policyText), // jev & laya share the model-stripped body, so their prompt_hash is identical
      options: c.optionPairs, expected: c.expected,
      request_model: arm === 'jev' ? JEV_MODEL : arm === 'decisions' ? DECISIONS_MODEL : arm === 'semif' ? SEMIF_MODEL : pinned, api_model: typeof b?.model === 'string' ? b.model : null,
      raw_answer: raw, choice, lenient_choice: arm === 'qwen' ? lenient : null, valid, failure_reason: valid ? null : failure,
      abstain_key: abstainKey, abstained: valid && abstainKey !== null && choice === abstainKey, correct: valid && choice === c.expected,
      probabilities: arm === 'qwen' ? null : probabilities, confidence: arm === 'qwen' ? null : confidence, chosen_probability: arm === 'qwen' ? null : chosenP,
      answer_type: arm === 'qwen' ? null : (b?.answers?.choice?.type ?? null),
      attempts: atts.length, retries: atts.length - 1,
      latency_ms_total: outcome.t_end_ms - outcome.t_start_ms, latency_ms_final: lastLat,
      prompt_tokens: promptTok, completion_tokens: complTok,
      e2e_completion_tok_per_s: complTok !== null && lastLat ? complTok / (lastLat / 1000) : null,
      e2e_tok_per_s_label: 'end-to-end: completion_tokens / (latency_ms_final/1000), client-observed',
      reported_cost_usd: billed ? reported : null, cost_known: billed ? known : false,
      charged_usd: billed ? atts.reduce((s, a) => s + a.charged_usd, 0) : null,
      cost_label: arm === 'qwen' ? (qwenBilled ? 'openrouter usage.cost summed over attempts; unknown attempts charged at reservation' : QWEN_COST_LABEL) : arm === 'decisions' ? DECISIONS_COST_LABEL : arm === 'semif' ? SEMIF_COST_LABEL : 'openrouter usage.cost summed over attempts; unknown attempts charged at reservation',
      concurrency: conc,
    };
    return { outcome, result };
  }

  async function runTrial(t: Trial, conc: number) {
    const c = cases.get(t.case_id)!;
    const results: Record<string, any> = {};
    for (const arm of t.arm_order) {
      if (runCtl.signal.aborted || stopReason) return;
      const { outcome, result } = await runArm(arm, t, c, conc);
      if (outcome.kind === 'aborted') return;
      if (outcome.kind === 'budget_block') { drain('budget: USD cap reached', 'budget_block', { trial_id: t.trial_id, arm, budget: budget.snapshot() }); return; }
      if (outcome.kind === 'drift') { event('drift', { source: 'response_model', trial_id: t.trial_id, arm, pinned, served: outcome.drift_model }); requestStop('model drift (response model)'); return; }
      if (outcome.kind === 'fatal') { event('error', { error: 'fatal_http', arm, trial_id: t.trial_id, http_status: outcome.http_status, detail: outcome.error }); requestStop(`fatal HTTP ${outcome.http_status} from ${arm}`); return; }
      W.results.write(result); st.results++;
      (result.valid ? st.valid : st.invalid)[arm]++;
      if (result.latency_ms_final !== null) { st.lat[arm].push(result.latency_ms_final); if (st.lat[arm].length > 200) st.lat[arm].shift(); }
      results[arm] = result;
      if (consecutiveTransport[arm] >= maxConsec) { event('error', { error: 'circuit_breaker', arm, consecutive_transport_failures: consecutiveTransport[arm] }); requestStop(`${arm}: ${consecutiveTransport[arm]} consecutive transport failures`); }
    }
    for (const a of t.arm_order) if (!results[a]) return;
    const pair: any = { trial_id: t.trial_id, case_id: t.case_id, phase: t.phase, rep: t.rep, arm_order: t.arm_order, input_hash: results[t.arm_order[0]].input_hash, expected: c.expected, concurrency: conc, segment, t_end_utc: nowUtc() };
    if (results.jev) { pair.jev_result_id = results.jev.result_id; pair.jev_choice = results.jev.choice; pair.jev_valid = results.jev.valid; pair.jev_correct = results.jev.correct; pair.jev_abstained = results.jev.abstained; }
    if (results.qwen) { pair.qwen_result_id = results.qwen.result_id; pair.qwen_choice = results.qwen.choice; pair.qwen_valid = results.qwen.valid; pair.qwen_correct = results.qwen.correct; pair.qwen_abstained = results.qwen.abstained; }
    if (results.decisions) { pair.decisions_result_id = results.decisions.result_id; pair.decisions_choice = results.decisions.choice; pair.decisions_valid = results.decisions.valid; pair.decisions_correct = results.decisions.correct; pair.decisions_abstained = results.decisions.abstained; }
    if (results.jev && results.qwen) { pair.agree = results.jev.choice === results.qwen.choice; pair.both_invalid = !results.jev.valid && !results.qwen.valid; }
    W.pairs.write(pair);
    completed.add(t.trial_id); st.pairs++;
    pairsByPhase[t.phase] = (pairsByPhase[t.phase] ?? 0) + 1;
  }

  let remainingLimit = opts.limit ?? Infinity;
  let pairsWrittenAtStart = st.pairs;
  try {
    for (const phase of phases) {
      if (stopReason || drainReason) break;
      const conc = phaseConcurrency(phase, opts.concurrency);
      const todo = selected.get(phase)!.filter((t) => !completed.has(t.trial_id));
      const queue = todo.slice(0, Math.max(0, Math.min(todo.length, remainingLimit)));
      remainingLimit -= queue.length;
      st.phase = phase; st.concurrency = conc;
      event('phase_start', { phase, concurrency: conc, planned: selected.get(phase)!.length, already_completed: selected.get(phase)!.length - todo.length, dispatch_queue: queue.length });
      let idx = 0;
      const worker = async () => {
        while (!stopReason && !drainReason && idx < queue.length) {
          const t = queue[idx++];
          const adm = budget.admitTrial(t.trial_id);
          if (!adm.ok) {
            if (adm.reason === 'max_trials') drain('budget: max trials reached', 'budget_block', { cap: 'max_trials', trial_id: t.trial_id, trials_admitted: budget.admitted.size });
            else if (adm.reason === 'deadline') drain('deadline (dispatch margin)', 'deadline_drain', { deadline_utc: caps.deadline_utc, margin_ms: deadlineMarginMs });
            break;
          }
          st.inflight++;
          try { await runTrial(t, conc); }
          catch (e) { event('error', { error: 'trial_exception', trial_id: t.trial_id, detail: redactText((e as any)?.stack ?? e, 500) }); requestStop('internal error'); }
          finally { st.inflight--; }
        }
      };
      await Promise.all(Array.from({ length: Math.min(conc, Math.max(1, queue.length)) }, worker));
      event('phase_end', { phase, pairs_completed_total: pairsByPhase[phase] ?? 0, stop_reason: stopReason, drain_reason: drainReason });
      if (remainingLimit <= 0 && !stopReason) { event('note', { note: 'limit reached' }); break; }
    }
  } finally {
    clearInterval(stopPoll); clearInterval(statusTimer); clearInterval(healthTimer);
    deps.signal?.removeEventListener('abort', onExternal);
    const endMs = Date.now();
    writeStatus(true);
    W.segments.write({ type: 'segment_end', segment, t_utc: nowUtc(endMs), t_local: localIso(endMs), stop_reason: stopReason, drain_reason: drainReason, pairs: st.pairs - pairsWrittenAtStart, results: st.results, attempts: st.attempts, budget: budget.snapshot() });
    event('segment_end', { stop_reason: stopReason, drain_reason: drainReason });
    event('run_end', { stop_reason: stopReason ?? drainReason ?? 'completed', hard_stop: !!stopReason, pairs_this_segment: st.pairs, results_this_segment: st.results, attempts_this_segment: st.attempts, budget: budget.snapshot() });
    for (const w of Object.values(W)) w.close();
  }
  return { exitCode: signalled ? 130 : 0, stopReason: stopReason ?? drainReason, segment, pairsWritten: st.pairs, resultsWritten: st.results, attemptsWritten: st.attempts, runDir: opts.runDir };
}

// ---------------- CLI ----------------
export function parseArgs(argv: string[]): RunOptions {
  const a: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (!k.startsWith('--')) throw new Error(`unexpected argument ${k}`);
    const name = k.slice(2);
    if (name === 'dry-run') { a[name] = true; continue; }
    const v = argv[++i];
    if (v === undefined) throw new Error(`missing value for ${k}`);
    a[name] = v;
  }
  const req = (n: string) => { if (typeof a[n] !== 'string') throw new Error(`--${n} is required`); return a[n] as string; };
  const int = (n: string) => (a[n] === undefined ? undefined : (() => { const x = Number(a[n]); if (!Number.isInteger(x)) throw new Error(`--${n} must be an integer`); return x; })());
  const phasesArg = (a['phases'] ?? a['phase']) as string | undefined;
  if (!phasesArg) throw new Error('--phases is required');
  const known = new Set(['run-dir', 'plan', 'bank', 'policy', 'phases', 'phase', 'concurrency', 'max-trials', 'max-usd', 'deadline', 'qwen-base', 'qwen-model', 'expect-qwen-id', 'decisions-base', 'laya-base', 'semif-base', 'dry-run', 'limit']);
  for (const k of Object.keys(a)) if (!known.has(k)) throw new Error(`unknown option --${k}`);
  if (a['laya-base'] !== undefined && a['decisions-base'] !== undefined) throw new Error('pass either --decisions-base or --laya-base (alias), not both');
  return {
    runDir: typeof a['run-dir'] === 'string' ? (a['run-dir'] as string) : '', planPath: req('plan'), bankPath: req('bank'), policyPath: req('policy'),
    phases: phasesArg.split(',').map((s) => s.trim()).filter(Boolean),
    concurrency: int('concurrency') ?? 1, maxTrials: int('max-trials'),
    maxUsd: a['max-usd'] === undefined ? undefined : Number(a['max-usd']),
    deadline: a['deadline'] as string | undefined, qwenBase: a['qwen-base'] as string | undefined, qwenModel: a['qwen-model'] as string | undefined,
    decisionsBase: (a['decisions-base'] ?? a['laya-base']) as string | undefined, semifBase: a['semif-base'] as string | undefined,
    expectQwenId: (a['expect-qwen-id'] as string | undefined) ?? null, dryRun: !!a['dry-run'], limit: int('limit') ?? null,
  };
}

async function main() {
  let opts: RunOptions;
  try { opts = parseArgs(process.argv.slice(2)); } catch (e) { process.stderr.write(`[runner] ${(e as Error).message}\n`); process.exit(2); }
  if (!opts.runDir) {
    // default --run-dir: runs/<arms>-<UTC timestamp>
    let arms = '';
    try {
      const plan = JSON.parse(fs.readFileSync(opts.planPath!, 'utf8'));
      arms = [...new Set(plan.trials.flatMap((t: any) => t.arm_order))].sort().join('-');
    } catch { /* keep the generic default */ }
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    opts.runDir = path.join('runs', `${arms ? arms + '-' : ''}${stamp}`);
  }
  const ctl = new AbortController();
  let n = 0;
  const onSig = (sig: string) => { n++; if (n > 1) { process.stderr.write('[runner] second signal: exiting now\n'); process.exit(130); } ctl.abort(sig); };
  process.on('SIGINT', () => onSig('SIGINT'));
  // SIGTERM: Node maps it on all platforms; on Windows also handle the Service-style stop quietly.
  process.on('SIGTERM', () => onSig('SIGTERM'));
  try {
    const s = await run(opts, { signal: ctl.signal });
    process.stdout.write(JSON.stringify({ run_dir: s.runDir, segment: s.segment, stop_reason: s.stopReason, pairs: s.pairsWritten, results: s.resultsWritten, attempts: s.attemptsWritten }) + '\n');
    process.exit(s.exitCode);
  } catch (e) {
    process.stderr.write(`[runner] fatal: ${redactText((e as Error)?.message ?? e, 1000)}\n`);
    process.exit(1);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) void main();
