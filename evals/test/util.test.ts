// PLAN-PUBLIC-REPO R3.8: credential() tests. The real key must never appear in any log line or file.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { credential } from '../util.ts';

function envRoot(env: Record<string, string | undefined>, dotenv?: string): [string, () => void] {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cred-'));
  if (dotenv !== undefined) fs.writeFileSync(path.join(dir, '.env'), dotenv);
  const saved = process.env.OPENROUTER_API_KEY;
  if (env.OPENROUTER_API_KEY === undefined) delete process.env.OPENROUTER_API_KEY;
  else process.env.OPENROUTER_API_KEY = env.OPENROUTER_API_KEY;
  return [dir, () => { if (saved === undefined) delete process.env.OPENROUTER_API_KEY; else process.env.OPENROUTER_API_KEY = saved; }];
}

test('credential: from env var', () => {
  const [dir, restore] = envRoot({ OPENROUTER_API_KEY: 'sk-or-v1-envsecret' });
  try { assert.equal(credential(dir), 'sk-or-v1-envsecret'); } finally { restore(); }
});

test('credential: from .env file when env var unset', () => {
  const [dir, restore] = envRoot({}, 'OPENROUTER_API_KEY=sk-or-v1-filesecret\n');
  try { assert.equal(credential(dir), 'sk-or-v1-filesecret'); } finally { restore(); }
});

test('credential: env var wins over .env', () => {
  const [dir, restore] = envRoot({ OPENROUTER_API_KEY: 'sk-or-v1-envsecret' }, 'OPENROUTER_API_KEY=sk-or-v1-filesecret\n');
  try { assert.equal(credential(dir), 'sk-or-v1-envsecret'); } finally { restore(); }
});

test('credential: missing everywhere throws a clear error', () => {
  const [dir, restore] = envRoot({});
  try { assert.throws(() => credential(dir), /no API key/); } finally { restore(); }
});

test('credential: malformed key throws and is never printed', () => {
  const [dir, restore] = envRoot({ OPENROUTER_API_KEY: 'gabagaba' });
  try {
    assert.throws(() => credential(dir), (e: any) => /malformed/.test(e.message) && !e.message.includes('gabagaba'));
  } finally { restore(); }
});
