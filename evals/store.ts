// Durable run-directory storage (SPEC §7).
import fs from 'node:fs';
import path from 'node:path';

export class JsonlWriter {
  private fd: number; private lastSync = 0; private dirty = false;
  readonly file: string; readonly fsyncEveryMs: number;
  constructor(file: string, fsyncEveryMs = 2000) {
    this.file = file; this.fsyncEveryMs = fsyncEveryMs;
    // If a previous crash left a partial trailing line, terminate it so new lines stay parseable.
    if (fs.existsSync(file)) {
      const st = fs.statSync(file);
      if (st.size > 0) {
        const fdr = fs.openSync(file, 'r'); const b = Buffer.alloc(1);
        fs.readSync(fdr, b, 0, 1, st.size - 1); fs.closeSync(fdr);
        if (b[0] !== 0x0a) fs.appendFileSync(file, '\n');
      }
    }
    this.fd = fs.openSync(file, 'a');
  }
  write(obj: unknown) {
    fs.writeSync(this.fd, JSON.stringify(obj) + '\n');
    this.dirty = true;
    const t = Date.now();
    if (t - this.lastSync >= this.fsyncEveryMs) this.sync();
  }
  sync() { if (this.dirty) { try { fs.fsyncSync(this.fd); } catch { /* ignore */ } this.dirty = false; } this.lastSync = Date.now(); }
  close() { this.sync(); try { fs.closeSync(this.fd); } catch { /* already closed */ } }
}

/** Tolerant JSONL reader: unparseable lines (e.g. a torn final line) are counted, not fatal. */
export function readJsonl<T = any>(file: string): { rows: T[]; bad: number } {
  if (!fs.existsSync(file)) return { rows: [], bad: 0 };
  const rows: T[] = []; let bad = 0;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try { rows.push(JSON.parse(line)); } catch { bad++; }
  }
  return { rows, bad };
}

export function writeJsonAtomic(file: string, obj: unknown) {
  const tmp = path.join(path.dirname(file), `.${path.basename(file)}.${process.pid}.tmp`);
  const fd = fs.openSync(tmp, 'w');
  try { fs.writeSync(fd, JSON.stringify(obj, null, 2) + '\n'); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  fs.renameSync(tmp, file);
}

/** Write-once: throws EEXIST rather than overwrite. */
export function writeJsonOnce(file: string, obj: unknown) {
  const fd = fs.openSync(file, 'wx');
  try { fs.writeSync(fd, JSON.stringify(obj, null, 2) + '\n'); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
}
