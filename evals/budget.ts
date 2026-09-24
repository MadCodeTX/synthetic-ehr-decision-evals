// Synchronous budget (SPEC §8). All mutations are synchronous so concurrent async workers cannot race.
// Money is tracked in integer nano-USD to avoid float drift; reported costs are rounded UP.
const NANO = 1e9;
const toNano = (usd: number) => Math.ceil(usd * NANO - 1e-6);
const toUsd = (n: number) => n / NANO;

export type BudgetCaps = { maxUsd: number; maxTrials: number; deadlineMs: number; deadlineMarginMs?: number };
export type Reservation = { id: string; nano: number };

export class Budget {
  readonly maxNano: number; readonly maxTrials: number; readonly deadlineMs: number; readonly deadlineMarginMs: number;
  spentReportedNano = 0; unknownChargedNano = 0; reservedNano = 0;
  admitted = new Set<string>();
  reservationsMade = 0; refusedReservations = 0; refusedTrials = 0;
  stopped = false; stopReason: string | null = null;
  private open = new Map<string, number>();
  private seq = 0;
  now: () => number;

  constructor(caps: BudgetCaps, now: () => number = Date.now) {
    if (!(caps.maxUsd >= 0) || !Number.isFinite(caps.maxUsd)) throw new Error('maxUsd must be finite >= 0');
    this.maxNano = Math.floor(caps.maxUsd * NANO + 1e-6);
    this.maxTrials = caps.maxTrials; this.deadlineMs = caps.deadlineMs; this.deadlineMarginMs = caps.deadlineMarginMs ?? 0;
    this.now = now;
  }

  /** Restore cumulative state from prior segments. */
  restore(prior: { reportedUsd: number; unknownUsd: number; trialIds: Iterable<string> }) {
    this.spentReportedNano += toNano(prior.reportedUsd);
    this.unknownChargedNano += toNano(prior.unknownUsd);
    for (const t of prior.trialIds) this.admitted.add(t);
  }

  stop(reason: string) { if (!this.stopped) { this.stopped = true; this.stopReason = reason; } }

  pastDispatchDeadline(): boolean { return this.now() >= this.deadlineMs - this.deadlineMarginMs; }

  /** Admit a paired trial. Re-admitting an id already counted (resume of an incomplete trial) is free. */
  admitTrial(trialId: string): { ok: true } | { ok: false; reason: string } {
    if (this.stopped) return { ok: false, reason: 'stopped:' + this.stopReason };
    if (this.pastDispatchDeadline()) return { ok: false, reason: 'deadline' };
    if (this.admitted.has(trialId)) return { ok: true };
    if (this.admitted.size >= this.maxTrials) { this.refusedTrials++; return { ok: false, reason: 'max_trials' }; }
    this.admitted.add(trialId);
    return { ok: true };
  }

  committedNano() { return this.spentReportedNano + this.unknownChargedNano + this.reservedNano; }

  /** Reserve est USD for one paid attempt. Returns null if the cap would be exceeded. */
  reserveCall(estUsd: number): Reservation | null {
    const n = toNano(estUsd);
    if (!(n >= 0)) throw new Error('bad reservation');
    if (this.committedNano() + n > this.maxNano) { this.refusedReservations++; return null; }
    const id = 'r' + (++this.seq);
    this.open.set(id, n); this.reservedNano += n; this.reservationsMade++;
    return { id, nano: n };
  }

  /** Settle a reservation. Finite >=0 reported cost is spent; anything else keeps the full reservation as unknown. */
  settle(id: string, reportedCost: unknown): { cost_known: boolean; charged_usd: number; reported_cost_usd: number | null } {
    const n = this.open.get(id);
    if (n === undefined) throw new Error(`settle: unknown or already-settled reservation ${id}`);
    this.open.delete(id); this.reservedNano -= n;
    if (typeof reportedCost === 'number' && Number.isFinite(reportedCost) && reportedCost >= 0) {
      const c = toNano(reportedCost);
      this.spentReportedNano += c;
      return { cost_known: true, charged_usd: toUsd(c), reported_cost_usd: reportedCost };
    }
    this.unknownChargedNano += n;
    return { cost_known: false, charged_usd: toUsd(n), reported_cost_usd: null };
  }

  snapshot() {
    return {
      max_usd: toUsd(this.maxNano), max_trials: this.maxTrials, deadline_utc: new Date(this.deadlineMs).toISOString(),
      spent_reported_usd: toUsd(this.spentReportedNano), unknown_cost_charged_usd: toUsd(this.unknownChargedNano),
      reserved_outstanding_usd: toUsd(this.reservedNano), committed_usd: toUsd(this.committedNano()),
      open_reservations: this.open.size, reservations_made: this.reservationsMade,
      refused_reservations: this.refusedReservations, trials_admitted: this.admitted.size, refused_trials: this.refusedTrials,
      stopped: this.stopped, stop_reason: this.stopReason,
    };
  }
}
