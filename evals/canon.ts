// Canonical JSON (SPEC §4). canon() is byte-identical to Python
//   json.dumps(x, sort_keys=True, separators=(',',':'), ensure_ascii=False)
// for JSON-parsed data (strings, ints, non-integral floats, bools, null, arrays, objects).
// Options are never passed as an object: they are converted to [key, description] pairs in presented
// order (optionsPairs), which is how the "options keep insertion order" rule is realised in both languages.
import { sha256 } from './util.ts';

function cmpCodePoints(a: string, b: string): number {
  // Python sorts str by code point; JS default sort is by UTF-16 unit. Compare by code point.
  const ia = a[Symbol.iterator](), ib = b[Symbol.iterator]();
  for (;;) {
    const x = ia.next(), y = ib.next();
    if (x.done || y.done) return x.done && y.done ? 0 : x.done ? -1 : 1;
    const cx = x.value.codePointAt(0)!, cy = y.value.codePointAt(0)!;
    if (cx !== cy) return cx - cy;
  }
}

export function canon(x: unknown): string {
  if (x === null) return 'null';
  switch (typeof x) {
    case 'string': return JSON.stringify(x);
    case 'boolean': return x ? 'true' : 'false';
    case 'number':
      if (!Number.isFinite(x)) throw new Error('canon: non-finite number');
      if (Object.is(x, -0)) return '0';
      return JSON.stringify(x);
    case 'object': {
      if (Array.isArray(x)) return '[' + x.map(canon).join(',') + ']';
      const keys = Object.keys(x as object).filter((k) => (x as any)[k] !== undefined).sort(cmpCodePoints);
      return '{' + keys.map((k) => JSON.stringify(k) + ':' + canon((x as any)[k])).join(',') + '}';
    }
    default: throw new Error(`canon: unsupported type ${typeof x}`);
  }
}

/** input_hash per SPEC §4. `optionPairs` MUST be in presented order (see extractOptionPairs). */
export function inputHash(workflow: string, evidence: unknown, optionPairs: [string, string][], policyText: string): string {
  return sha256(canon({ workflow, evidence, options_pairs: optionPairs, policy_text: policyText }));
}

// ---- Order-preserving option extraction ------------------------------------------------------------
// JS objects enumerate integer-like keys ("10825739") first in ascending numeric order, so JSON.parse
// loses the presented option order for such keys. We therefore read `options` straight from the raw
// JSON text as ordered [key, description] pairs, and serialize Jev `criteria` from those pairs.

function skipWs(t: string, i: number): number { while (i < t.length && ' \t\n\r'.includes(t[i])) i++; return i; }
function scanString(t: string, i: number): number {         // t[i] === '"'; returns index after closing quote
  if (t[i] !== '"') throw new Error(`json scan: expected string at ${i}`);
  for (i++; i < t.length; i++) { if (t[i] === '\\') i++; else if (t[i] === '"') return i + 1; }
  throw new Error('json scan: unterminated string');
}
function scanValue(t: string, i: number): number {          // returns index after the value
  i = skipWs(t, i);
  const ch = t[i];
  if (ch === '"') return scanString(t, i);
  if (ch === '{' || ch === '[') {
    let depth = 0;
    for (; i < t.length; i++) {
      const c = t[i];
      if (c === '"') { i = scanString(t, i) - 1; continue; }
      if (c === '{' || c === '[') depth++;
      else if (c === '}' || c === ']') { depth--; if (depth === 0) return i + 1; }
    }
    throw new Error('json scan: unterminated container');
  }
  const m = /^(?:-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)/.exec(t.slice(i, i + 64));
  if (!m) throw new Error(`json scan: bad value at ${i}`);
  return i + m[0].length;
}
/** Ordered [key, rawValueText] entries of the JSON object starting at t[i]. */
function objectEntries(t: string, i: number): [string, string][] {
  i = skipWs(t, i);
  if (t[i] !== '{') throw new Error('json scan: expected object');
  const out: [string, string][] = [];
  i = skipWs(t, i + 1);
  if (t[i] === '}') return out;
  for (;;) {
    i = skipWs(t, i);
    const ke = scanString(t, i); const key = JSON.parse(t.slice(i, ke));
    i = skipWs(t, ke); if (t[i] !== ':') throw new Error('json scan: expected :');
    const vs = skipWs(t, i + 1); const ve = scanValue(t, vs);
    out.push([key, t.slice(vs, ve)]);
    i = skipWs(t, ve);
    if (t[i] === ',') { i++; continue; }
    if (t[i] === '}') return out;
    throw new Error('json scan: expected , or }');
  }
}
/** Presented-order option pairs from one raw bank.jsonl line. */
export function extractOptionPairs(line: string): [string, string][] {
  const top = objectEntries(line, 0);
  const opt = top.filter(([k]) => k === 'options');
  if (opt.length !== 1) throw new Error('bank line: expected exactly one top-level "options"');
  const pairs = objectEntries(opt[0][1], 0).map(([k, v]) => [k, JSON.parse(v)] as [string, string]);
  if (new Set(pairs.map(([k]) => k)).size !== pairs.length) throw new Error('bank line: duplicate option key');
  return pairs;
}
/** JSON object text with keys in the given order (JSON.stringify would hoist integer-like keys). */
export function orderedObjectJson(pairs: [string, unknown][]): string {
  return '{' + pairs.map(([k, v]) => JSON.stringify(k) + ':' + JSON.stringify(v)).join(',') + '}';
}
