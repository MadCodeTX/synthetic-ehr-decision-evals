// Regenerates SHA256SUMS over data/** and policy/policy.json (paths relative to the repo root).
// npm run checksums
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function sha256File(p: string): string {
  return crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
}

function listFiles(dir: string, base = ROOT): string[] {
  const out: string[] = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...listFiles(p, base));
    else out.push(p);
  }
  return out.sort();
}

const files = [
  ...listFsSafe(path.join(ROOT, 'data')),
  path.join(ROOT, 'policy', 'policy.json'),
];
function listFsSafe(p: string): string[] {
  return fs.existsSync(p) ? listFiles(p) : [];
}
// data/v2/bank_v2.jsonl is the git-ignored unpacked copy of bank_v2.jsonl.gz (verified by data/v2/unpack.py).
const tracked = files.filter((p) => path.relative(ROOT, p).split(path.sep).join('/') !== 'data/v2/bank_v2.jsonl');
const lines = tracked.map((p) => `${sha256File(p)}  ${path.relative(ROOT, p).split(path.sep).join('/')}`);
fs.writeFileSync(path.join(ROOT, 'SHA256SUMS'), lines.join('\n') + '\n');
process.stdout.write(`SHA256SUMS written: ${lines.length} files\n`);
