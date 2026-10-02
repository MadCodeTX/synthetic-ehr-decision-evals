// Emit the EXACT decisions/systemone request bodies the harness sends, for every v1 DEV case whose
// expected label is an offered option (the 4,084-item Laya-FT / CLM-FT training set).
// Uses the harness's own code (evals/clients.ts decisionsBodyText + canon.ts extractOptionPairs), read-only.
// Holdout / v2 cases are never emitted.
// Usage: node --experimental-strip-types build_bodies.ts <repo> <out.jsonl>
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
const repo = process.argv[2], out = process.argv[3];
const { decisionsBodyText } = await import(pathToFileURL(path.join(repo, 'evals/clients.ts')).href);
const { extractOptionPairs } = await import(pathToFileURL(path.join(repo, 'evals/canon.ts')).href);
const policy = JSON.parse(fs.readFileSync(path.join(repo, 'policy/policy.json'), 'utf8'));
const lines = fs.readFileSync(path.join(repo, 'data/bank.jsonl'), 'utf8').split('\n');
const fh = fs.openSync(out, 'w');
let n = 0, skipped = 0, holdout = 0;
for (const line of lines) {
  if (!line.trim()) continue;
  const c = JSON.parse(line);
  if (c.split !== 'dev') { holdout++; continue; }
  if (!Object.hasOwn(c.options, c.expected)) { skipped++; continue; }
  const pairs = extractOptionPairs(line);
  const body = decisionsBodyText(c.evidence, pairs, policy.workflows[c.workflow].policy_text);
  fs.writeSync(fh, JSON.stringify({ case_id: c.case_id, workflow: c.workflow, stratum: c.stratum,
    family_id: c.family_id ?? c.case_id, expected: c.expected, body }) + '\n');
  n++;
}
fs.closeSync(fh);
console.log(JSON.stringify({ emitted: n, skipped_no_valid_option: skipped, non_dev_skipped: holdout }));
