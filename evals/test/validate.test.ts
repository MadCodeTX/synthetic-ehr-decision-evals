import test from 'node:test';
import assert from 'node:assert/strict';
import { validateJev, validateQwen, lenientChoice } from '../validate.ts';

const keys = ['a', 'b', 'needs_human_review'];
const good = () => ({ answers: { choice: { type: 'choice', choice: 'a', probabilities: { a: 0.7, b: 0.2, needs_human_review: 0.1 }, confidence: 0.7 } } });
const mut = (f: (x: any) => void) => { const g: any = good(); f(g.answers.choice); return g; };

test('jev: well-formed decision is valid', () => {
  const v = validateJev(good(), keys);
  assert.equal(v.valid, true); assert.equal(v.choice, 'a'); assert.equal(v.chosen_probability, 0.7); assert.equal(v.failure_reason, null);
});

test('jev: malformed decisions are rejected with reasons and choice=null', () => {
  const cases: [string, any, string][] = [
    ['wrong type', mut((c) => { c.type = 'text'; }), 'wrong_type'],
    ['missing type', mut((c) => { delete c.type; }), 'wrong_type'],
    ['choice not in options', mut((c) => { c.choice = 'zzz'; }), 'choice_not_in_options'],
    ['choice case mismatch', mut((c) => { c.choice = 'A'; }), 'choice_not_in_options'],
    ['choice number', mut((c) => { c.choice = 1; }), 'choice_not_string'],
    ['extra probability key', mut((c) => { c.probabilities.extra = 0; }), 'probabilities_extra_key'],
    ['missing probability key', mut((c) => { delete c.probabilities.b; }), 'probabilities_missing_key'],
    ['NaN probability', mut((c) => { c.probabilities.b = NaN; }), 'probability_not_finite_in_0_1'],
    ['Infinity probability', mut((c) => { c.probabilities.b = Infinity; }), 'probability_not_finite_in_0_1'],
    ['-0.1 probability', mut((c) => { c.probabilities.b = -0.1; }), 'probability_not_finite_in_0_1'],
    ['1.2 probability', mut((c) => { c.probabilities.b = 1.2; }), 'probability_not_finite_in_0_1'],
    ['string-number probability', mut((c) => { c.probabilities.b = '0.2'; }), 'probability_not_finite_in_0_1'],
    ['null probability', mut((c) => { c.probabilities.b = null; }), 'probability_not_finite_in_0_1'],
    ['probabilities array', mut((c) => { c.probabilities = [0.7, 0.2, 0.1]; }), 'probabilities_not_object'],
    ['probabilities missing', mut((c) => { delete c.probabilities; }), 'probabilities_not_object'],
    ['missing confidence', mut((c) => { delete c.confidence; }), 'missing_confidence'],
    ['string confidence', mut((c) => { c.confidence = '0.7'; }), 'confidence_not_finite_in_0_1'],
    ['NaN confidence', mut((c) => { c.confidence = NaN; }), 'confidence_not_finite_in_0_1'],
    ['confidence 1.2', mut((c) => { c.confidence = 1.2; }), 'confidence_not_finite_in_0_1'],
    ['no answers', { usage: {} }, 'missing_answers_choice'],
    ['null body', null, 'missing_answers_choice'],
  ];
  for (const [name, body, reason] of cases) {
    const v = validateJev(body, keys);
    assert.equal(v.valid, false, name); assert.equal(v.choice, null, name); assert.equal(v.failure_reason, reason, name);
  }
});

test('qwen: strict vs lenient parsing', () => {
  const k = ['opt_a', 'opt_b', 'needs_human_review'];
  const t = (content: unknown) => validateQwen(content, k);
  let v = t('{"choice":"opt_a"}'); assert.equal(v.valid, true); assert.equal(v.choice, 'opt_a'); assert.equal(v.lenient_choice, 'opt_a');
  v = t('  {"choice": "opt_b"}\n'); assert.equal(v.valid, true, 'surrounding whitespace is trimmed');
  v = t('Sure! {"choice":"opt_b"} is right.'); assert.equal(v.valid, false); assert.equal(v.failure_reason, 'not_json'); assert.equal(v.choice, null); assert.equal(v.lenient_choice, 'opt_b');
  v = t('```json\n{"choice":"opt_a"}\n```'); assert.equal(v.valid, false); assert.equal(v.lenient_choice, 'opt_a');
  v = t('{"choice":"opt_a","reason":"x"}'); assert.equal(v.valid, false); assert.equal(v.failure_reason, 'extra_keys'); assert.equal(v.lenient_choice, 'opt_a');
  v = t('{"choice":"opt_z"}'); assert.equal(v.valid, false); assert.equal(v.failure_reason, 'choice_not_in_options'); assert.equal(v.lenient_choice, null);
  v = t('{"decision":"opt_a"}'); assert.equal(v.valid, false); assert.equal(v.failure_reason, 'missing_choice_key');
  v = t(''); assert.equal(v.valid, false); assert.equal(v.failure_reason, 'empty_content'); assert.equal(v.lenient_choice, null);
  v = t('   '); assert.equal(v.failure_reason, 'empty_content');
  v = t(null); assert.equal(v.failure_reason, 'no_content');
  v = t('["opt_a"]'); assert.equal(v.failure_reason, 'not_object');
  v = t('{"choice":1}'); assert.equal(v.failure_reason, 'choice_not_string');
  // lenient skips non-option matches and takes first option-key match
  assert.equal(lenientChoice('{"choice":"nope"} then {"choice":"needs_human_review"}', k), 'needs_human_review');
  assert.equal(lenientChoice('{"choice" : "opt_b"}', k), 'opt_b');
});
