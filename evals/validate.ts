// Strict response validation (SPEC §6). Invalid responses keep choice=null and stay in denominators.
import { isPlainObject } from './util.ts';

export type JevValidation = {
  valid: boolean; failure_reason: string | null; choice: string | null; answer_type: unknown;
  probabilities: Record<string, number> | null; confidence: number | null; chosen_probability: number | null;
};

const prob = (x: unknown) => typeof x === 'number' && Number.isFinite(x) && x >= 0 && x <= 1;

export function validateJev(body: unknown, optionKeys: string[]): JevValidation {
  const out: JevValidation = { valid: false, failure_reason: null, choice: null, answer_type: null, probabilities: null, confidence: null, chosen_probability: null };
  const fail = (r: string) => { out.failure_reason = r; return out; };
  const ans = (body as any)?.answers?.choice;
  if (!isPlainObject(ans)) return fail('missing_answers_choice');
  out.answer_type = ans.type ?? null;
  // Record well-formed numeric fields for analysis even if the decision as a whole is invalid.
  if (prob(ans.confidence)) out.confidence = ans.confidence as number;
  if (isPlainObject(ans.probabilities) && Object.values(ans.probabilities).every(prob)) out.probabilities = { ...(ans.probabilities as Record<string, number>) };
  if (ans.type !== 'choice') return fail('wrong_type');
  if (typeof ans.choice !== 'string') return fail('choice_not_string');
  if (!optionKeys.includes(ans.choice)) return fail('choice_not_in_options');
  if (!isPlainObject(ans.probabilities)) return fail('probabilities_not_object');
  const pk = Object.keys(ans.probabilities);
  const missing = optionKeys.filter((k) => !Object.hasOwn(ans.probabilities as object, k));
  const extra = pk.filter((k) => !optionKeys.includes(k));
  if (missing.length) return fail('probabilities_missing_key');
  if (extra.length) return fail('probabilities_extra_key');
  for (const k of pk) if (!prob((ans.probabilities as any)[k])) return fail('probability_not_finite_in_0_1');
  if (!('confidence' in ans)) return fail('missing_confidence');
  if (!prob(ans.confidence)) return fail('confidence_not_finite_in_0_1');
  out.valid = true; out.choice = ans.choice;
  out.chosen_probability = (ans.probabilities as any)[ans.choice];
  return out;
}

export type QwenValidation = { valid: boolean; failure_reason: string | null; choice: string | null; lenient_choice: string | null };

export function lenientChoice(content: unknown, optionKeys: string[]): string | null {
  if (typeof content !== 'string') return null;
  const re = /"choice"\s*:\s*"((?:[^"\\]|\\.)*)"/g;
  for (const m of content.matchAll(re)) {
    let k: string;
    try { k = JSON.parse('"' + m[1] + '"'); } catch { continue; }
    if (optionKeys.includes(k)) return k;
  }
  return null;
}

export function validateQwen(content: unknown, optionKeys: string[]): QwenValidation {
  const lenient_choice = lenientChoice(content, optionKeys);
  const out: QwenValidation = { valid: false, failure_reason: null, choice: null, lenient_choice };
  const fail = (r: string) => { out.failure_reason = r; return out; };
  if (typeof content !== 'string') return fail('no_content');
  const t = content.trim();
  if (!t) return fail('empty_content');
  let parsed: unknown;
  try { parsed = JSON.parse(t); } catch { return fail('not_json'); }
  if (!isPlainObject(parsed)) return fail('not_object');
  const keys = Object.keys(parsed);
  if (!keys.includes('choice')) return fail('missing_choice_key');
  if (keys.length !== 1) return fail('extra_keys');
  if (typeof parsed.choice !== 'string') return fail('choice_not_string');
  if (!optionKeys.includes(parsed.choice)) return fail('choice_not_in_options');
  out.valid = true; out.choice = parsed.choice;
  return out;
}
