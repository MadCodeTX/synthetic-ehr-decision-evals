// Qwen endpoint health + model pinning (SPEC §8). Jev is not pre-probed (a probe would cost money).
import type { FetchLike } from './clients.ts';
import { nowUtc, redactText } from './util.ts';

export type QwenProbe = {
  t_utc: string;
  health: { url: string; status: number | null; error: string | null };
  models: { url: string; status: number | null; error: string | null; ids: string[]; data_subset: Record<string, unknown>[] };
};

export function healthUrl(base: string): string { return base.replace(/\/+$/, '').replace(/\/v1$/, '') + '/health'; }
export function modelsUrl(base: string): string { return base.replace(/\/+$/, '') + '/models'; }

async function get(fetch: FetchLike, url: string, signal: AbortSignal, timeoutMs: number) {
  try {
    const r = await fetch(url, { method: 'GET', signal: AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]) });
    const text = await r.text();
    return { status: r.status, text, error: r.ok ? null : `http_${r.status}` };
  } catch (e) {
    return { status: null, text: '', error: redactText((e as any)?.name === 'TimeoutError' ? 'timeout' : (e as any)?.message ?? e, 200) };
  }
}

export async function probeQwen(base: string, fetch: FetchLike, signal: AbortSignal, timeoutMs = 10_000): Promise<QwenProbe> {
  const hu = healthUrl(base), mu = modelsUrl(base);
  const h = await get(fetch, hu, signal, timeoutMs);
  const m = await get(fetch, mu, signal, timeoutMs);
  let ids: string[] = [], subset: Record<string, unknown>[] = [], merr = m.error;
  if (m.status !== null && m.status >= 200 && m.status < 300) {
    try {
      const body = JSON.parse(m.text);
      const data = Array.isArray(body?.data) ? body.data : [];
      ids = data.map((d: any) => d?.id).filter((x: unknown): x is string => typeof x === 'string');
      subset = data.map((d: any) => ({ id: d?.id ?? null, object: d?.object ?? null, owned_by: d?.owned_by ?? null, root: d?.root ?? null, parent: d?.parent ?? null, max_model_len: d?.max_model_len ?? null, created: d?.created ?? null }));
      if (!ids.length) merr = 'no_models_listed';
    } catch { merr = 'bad_json'; }
  }
  return { t_utc: nowUtc(), health: { url: hu, status: h.status, error: h.error }, models: { url: mu, status: m.status, error: merr, ids, data_subset: subset } };
}

/** Pin the served model id. With an expected id it must be the first (served) id; without, exactly one id must be listed. */
export function pinModel(p: QwenProbe, expectId?: string | null): { ok: true; pinned: string } | { ok: false; reason: string } {
  if (p.models.error || !p.models.ids.length) return { ok: false, reason: `models endpoint unusable: ${p.models.error ?? 'empty'}` };
  if (expectId) {
    if (p.models.ids[0] !== expectId) return { ok: false, reason: `served model ${JSON.stringify(p.models.ids[0])} != expected ${JSON.stringify(expectId)}` };
    return { ok: true, pinned: expectId };
  }
  if (p.models.ids.length !== 1) return { ok: false, reason: `ambiguous model list (${p.models.ids.length} ids); pass --expect-qwen-id` };
  return { ok: true, pinned: p.models.ids[0] };
}

/** Drift = a successful /models response whose served (first) id is not the pinned id, or that omits it. */
export function isDrift(p: QwenProbe, pinned: string): boolean {
  if (p.models.error || !p.models.ids.length) return false;
  return p.models.ids[0] !== pinned || !p.models.ids.includes(pinned);
}

export function healthOk(p: QwenProbe): boolean {
  return p.health.status !== null && p.health.status >= 200 && p.health.status < 300 && !p.models.error;
}

// ---------------- decisions (self-hosted Jev-Decisions-compatible service) ----------------

export type DecisionsProbe = {
  t_utc: string;
  health: { url: string; status: number | null; error: string | null };
  ok: boolean;
  loaded: string[] | null;   // checkpoints preloaded, e.g. ["english", "multilingual", "typed-decisions"]
  cfg: Record<string, unknown> | null;  // tuned budget, e.g. {max_len: 1024, head_max_len: 512}
};

export async function probeDecisions(base: string, fetch: FetchLike, signal: AbortSignal, timeoutMs = 10_000): Promise<DecisionsProbe> {
  const url = base.replace(/\/+$/, '') + '/health';
  try {
    const r = await fetch(url, { method: 'GET', signal: AbortSignal.any([signal, AbortSignal.timeout(timeoutMs)]) });
    const text = await r.text();
    let parsed: any = null;
    try { parsed = JSON.parse(text); } catch { /* health body is not JSON */ }
    return {
      t_utc: nowUtc(), health: { url, status: r.status, error: r.ok ? null : `http_${r.status}` },
      ok: !!(r.ok && parsed?.ok === true),
      loaded: Array.isArray(parsed?.loaded) ? parsed.loaded : null,
      cfg: parsed && typeof parsed.cfg === 'object' && parsed.cfg !== null ? parsed.cfg : null,
    };
  } catch (e) {
    return { t_utc: nowUtc(), health: { url, status: null, error: redactText((e as any)?.name === 'TimeoutError' ? 'timeout' : (e as any)?.message ?? e, 200) }, ok: false, loaded: null, cfg: null };
  }
}
