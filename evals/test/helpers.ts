import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { RunOptions } from '../runner.ts';

export const FIX = path.join(path.dirname(fileURLToPath(import.meta.url)), 'fixtures');
export const QWEN_ID = 'Qwen/Qwen3.8-27B-FP8';

export function tmpDir(tag: string): string { return fs.mkdtempSync(path.join(os.tmpdir(), `ovn-${tag}-`)); }

export function baseOpts(runDir: string, over: Partial<RunOptions> = {}): RunOptions {
  return {
    runDir, planPath: path.join(FIX, 'plan.json'), bankPath: path.join(FIX, 'bank.jsonl'), policyPath: path.join(FIX, 'policy.json'),
    phases: ['pilot', 'broad', 'load_c2'], concurrency: 3, deadline: '2035-01-01T00:00:00Z', qwenBase: 'http://mockhost:8080/v1',
    qwenModel: QWEN_ID, decisionsBase: 'http://mockhost:8090',
    expectQwenId: QWEN_ID, timeouts: { jev_ms: 2000, qwen_ms: 2000 }, backoffScale: 0, stopPollMs: 20, statusIntervalMs: 50,
    healthIntervalMs: 60_000, ...over,
  };
}

export function readRows(file: string): any[] {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l)); // throws if any line is torn
}

export const quiet = { log: () => {} };

/** Response helper. */
export const J = (status: number, obj: unknown) => new Response(JSON.stringify(obj), { status, headers: { 'content-type': 'application/json' } });

/** A fetch that never resolves until its signal aborts. */
export function hang(signal?: AbortSignal): Promise<Response> {
  return new Promise((_, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    signal?.addEventListener('abort', () => reject(signal.reason), { once: true });
  });
}
