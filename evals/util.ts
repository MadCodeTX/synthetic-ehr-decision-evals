// Small shared helpers: hashing, timestamps, abortable sleep, secret redaction, credentials.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export function sha256(data: string | Uint8Array): string {
  return crypto.createHash('sha256').update(data).digest('hex');
}

export function nowUtc(ms: number = Date.now()): string { return new Date(ms).toISOString(); }

/** Local timezone of the running machine (the machine's IANA zone, e.g. one from the tz database); never hard-coded. */
export const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

const localFmt = new Intl.DateTimeFormat('en-US', {
  timeZone: LOCAL_TZ, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit',
});
/** ISO-8601 local time in the machine's timezone with numeric offset, e.g. 2026-09-22T20:54:00.123-05:00 */
export function localIso(ms: number = Date.now()): string {
  const p: Record<string, string> = {};
  for (const part of localFmt.formatToParts(new Date(ms))) p[part.type] = part.value;
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  const offMin = Math.round((asUtc - Math.floor(ms / 1000) * 1000) / 60000);
  const sign = offMin < 0 ? '-' : '+';
  const a = Math.abs(offMin);
  const msPart = String(((ms % 1000) + 1000) % 1000).padStart(3, '0');
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}.${msPart}${sign}${String(Math.floor(a / 60)).padStart(2, '0')}:${String(a % 60).padStart(2, '0')}`;
}

function parseEnvFile(repoRoot: string): Record<string, string> {
  const out: Record<string, string> = {};
  const p = path.join(repoRoot, '.env');
  if (!fs.existsSync(p)) return out;
  for (const line of fs.readFileSync(p, 'utf8').split(/\r?\n/)) {
    const m = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/.exec(line);
    if (!m) continue;
    let v = m[2];
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[m[1]] = v;
  }
  return out;
}

/**
 * The OpenRouter API key: `process.env.OPENROUTER_API_KEY`, or the value from a git-ignored
 * `.env` in the repo root. Must start with `sk-or-`. The value is never logged.
 */
export function credential(repoRoot?: string): string {
  const root = repoRoot ?? path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const key = process.env.OPENROUTER_API_KEY || parseEnvFile(root).OPENROUTER_API_KEY || '';
  if (!key) throw new Error('no API key: set OPENROUTER_API_KEY (env) or OPENROUTER_API_KEY in the repo .env file');
  if (!key.startsWith('sk-or-')) throw new Error('OPENROUTER_API_KEY is malformed: OpenRouter keys start with "sk-or-"');
  return key;
}

/** Abortable sleep; resolves false if aborted. */
export function sleep(ms: number, signal?: AbortSignal): Promise<boolean> {
  if (ms <= 0) return Promise.resolve(!signal?.aborted);
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve(false);
    const t = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(true); }, ms);
    const onAbort = () => { clearTimeout(t); resolve(false); };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/** Scrub anything that looks like a credential from free text before it is logged. */
export function redactText(s: unknown, max = 300): string {
  return String(s)
    .replace(/sk-or-[A-Za-z0-9_\-]+/g, '[REDACTED]')
    .replace(/sk-[A-Za-z0-9_\-]{8,}/g, '[REDACTED]')
    .replace(/(Bearer\s+)[^\s"',]+/gi, '$1[REDACTED]')
    .replace(/("?authorization"?\s*[:=]\s*)("[^"]*"|[^\s,}]+)/gi, '$1[REDACTED]')
    .slice(0, max);
}

/** Redact a header object (never log the result verbatim anyway). */
export function redactHeaders(h: Record<string, string> | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(h ?? {})) out[k] = /authorization|api-key|cookie|token/i.test(k) ? '[REDACTED]' : v;
  return out;
}

export function isPlainObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x) && Object.getPrototypeOf(x) === Object.prototype;
}
