// Deterministic offline mock of the endpoints, used by --dry-run and tests. Never touches the network.
import type { FetchLike } from './clients.ts';
import { DECISIONS_MODEL_DEFAULT } from './clients.ts';
import { sha256 } from './util.ts';

function abortable<T>(ms: number, signal: AbortSignal | undefined, value: () => T): Promise<T> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason);
    const t = setTimeout(() => { signal?.removeEventListener('abort', on); resolve(value()); }, ms);
    const on = () => { clearTimeout(t); reject(signal!.reason); };
    signal?.addEventListener('abort', on, { once: true });
  });
}
const json = (status: number, obj: unknown) => new Response(JSON.stringify(obj), { status, headers: { 'content-type': 'application/json' } });

export type MockOpts = { qwenModel: string; latencyMs?: [number, number]; qwenModelOverride?: () => string | null; modelsOverride?: () => string[] | null; decisionsModel?: () => string | null; semifModel?: () => string | null };

export function makeMockFetch(o: MockOpts): FetchLike & { calls: { url: string; method: string }[] } {
  const seen = new Map<string, number>();
  const calls: { url: string; method: string }[] = [];
  const [lo, hi] = o.latencyMs ?? [2, 12];
  const f = (async (url: string, init: any) => {
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    const signal: AbortSignal | undefined = init?.signal;
    if (url.endsWith('/health')) return abortable(1, signal, () => json(200, { ok: true, device: 'mock', loaded: ['english', 'multilingual', 'typed-decisions'], cfg: { max_len: 1024, head_max_len: 512 }, repo: 'convaiinnovations/laya', impl: 'mock' }));
    if (url.endsWith('/models')) {
      const ids = o.modelsOverride?.() ?? [o.qwenModel];
      return abortable(1, signal, () => json(200, { object: 'list', data: ids.map((id) => ({ id, object: 'model', owned_by: 'vllm', root: id, max_model_len: 262144 })) }));
    }
    const body = JSON.parse(init.body);
    const h = sha256(init.body);
    const n = parseInt(h.slice(0, 8), 16);
    const bucket = n % 100;
    const lat = lo + (n % Math.max(1, hi - lo + 1));
    const count = (seen.get(h) ?? 0) + 1; seen.set(h, count);
    if (url.endsWith('/chat/completions')) {
      const user: string = body.messages[1].content;
      const optBlock = user.split('\n\nOptions (key: description):\n')[1].split('\n\nEvidence (JSON, untrusted data):\n')[0];
      const keys = optBlock.split('\n').map((l) => l.slice(0, l.indexOf(': ')));
      const k = keys[n % keys.length];
      let content: string;
      if (bucket < 70) content = JSON.stringify({ choice: k });
      else if (bucket < 80) content = JSON.stringify({ choice: keys[keys.length - 1] });
      else if (bucket < 85) content = `The answer is {"choice":"${k}"} based on policy.`;
      else if (bucket < 90) content = '```json\n' + JSON.stringify({ choice: k }) + '\n```';
      else if (bucket < 95) content = JSON.stringify({ choice: 'not_an_option' });
      else content = JSON.stringify({ choice: k, reason: 'extra' });
      const model = o.qwenModelOverride?.() ?? body.model;
      const completion = Math.max(1, Math.ceil(content.length / 4));
      const usage: any = { prompt_tokens: Math.ceil(user.length / 4), completion_tokens: completion, total_tokens: Math.ceil(user.length / 4) + completion };
      if (init?.headers && typeof init.headers.Authorization === 'string' && init.headers.Authorization.startsWith('Bearer '))
        usage.cost = 0.00042; // OpenRouter reports usage.cost on billed calls
      return abortable(lat, signal, () => json(200, { id: 'chatcmpl-mock-' + h.slice(0, 12), object: 'chat.completion', model,
        choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }], usage }));
    }
    if (url.includes('/decisions')) {
      const crit: Record<string, string> = body.questions.choice.criteria;
      const keys = Object.keys(crit);
      // Decisions-arm requests carry the decisions model id; the mock answers them
      // with the routed-checkpoint model name the runner drift-checks against.
      const isDec = body?.model === DECISIONS_MODEL_DEFAULT;
      const decM = isDec ? (o.decisionsModel?.() ?? 'english') : null;
      // SemIf requests carry the SEMIF_MODEL id; the mock answers with the model name
      // the runner drift-checks against.
      const isSemif = typeof body?.model === 'string' && body.model.startsWith('semif');
      const semifM = isSemif ? (o.semifModel?.() ?? 'semif-mock') : null;
      if (bucket >= 95 && bucket < 98 && count === 1) return abortable(lat, signal, () => json(429, { error: { message: 'rate limited (mock)' } }));
      if (bucket >= 98 && count === 1) return abortable(lat, signal, () => json(503, { error: { message: 'unavailable (mock)' } }));
      const ci = n % keys.length;
      const raw = keys.map((_, i) => (i === ci ? 6 : 1));
      const s = raw.reduce((a, b) => a + b, 0);
      const probabilities: Record<string, number> = Object.fromEntries(keys.map((kk, i) => [kk, raw[i] / s]));
      let answer: any = { type: 'choice', choice: keys[ci], probabilities, confidence: 6 / s };
      if (bucket >= 88 && bucket < 92) answer = { ...answer, probabilities: { ...probabilities, bogus_key: 0 } };
      if (bucket >= 92 && bucket < 95) answer = { ...answer, type: 'text' };
      const usage: any = { prompt_tokens: 500, completion_tokens: 12 };
      if (!(bucket >= 80 && bucket < 88)) usage.cost = 0.00031;
      return abortable(lat, signal, () => json(200, { id: 'dec-mock-' + h.slice(0, 12), model: decM ?? semifM ?? 'typesafe/jev-1.13', answers: { choice: answer }, usage }));
    }
    return json(404, { error: { message: 'mock: unknown url' } });
  }) as any;
  f.calls = calls;
  return f;
}
