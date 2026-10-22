/**
 * In-memory stand-in for the API's data (PostgreSQL activity log + DynamoDB job table),
 * used by `npm run dev:mock` and the test suite. Shapes follow docs/CONTRACT.md.
 */
import { ACTIONS, AREAS } from '../api/types';
import type { ActivityEvent, ExportJob, ExportRequest } from '../api/types';

const DAY = 86_400_000;
const RETENTION_S = 7 * 24 * 3600;
const ENTITY_TYPES: Record<string, string[]> = {
  accounts: ['user', 'team', 'api_key'],
  billing: ['invoice', 'payment', 'subscription'],
  catalog: ['product', 'category', 'price_list'],
  orders: ['order', 'shipment', 'return'],
  settings: ['preference', 'webhook', 'integration'],
};

/** Deterministic PRNG so seeded data is stable between runs. */
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = <T>(rand: () => number, list: readonly T[]): T =>
  list[Math.floor(rand() * list.length)] as T;

export function seedEvents(count = 750, now = Date.now()): ActivityEvent[] {
  const rand = mulberry32(42);
  const events: ActivityEvent[] = [];
  let t = now;
  for (let i = count; i >= 1; i--) {
    t -= Math.floor(rand() * (90 * DAY)) / count;
    const area = pick(rand, AREAS);
    const entityType = pick(rand, ENTITY_TYPES[area] ?? ['record']);
    events.push({
      event_id: i,
      occurred_at: new Date(Math.floor(t)).toISOString(),
      actor_id: `usr_${Math.floor(rand() * 0xffffff)
        .toString(16)
        .padStart(6, '0')}`,
      area,
      action: pick(rand, ACTIONS),
      entity_type: entityType,
      entity_id: `${entityType.slice(0, 3)}_${Math.floor(rand() * 99_999)
        .toString()
        .padStart(5, '0')}`,
    });
  }
  return events; // newest first (occurred_at DESC, event_id DESC)
}

const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
let idCounter = 0;
export function ulid(now = Date.now()): string {
  let time = '';
  let ms = now;
  for (let i = 0; i < 10; i++) {
    time = CROCKFORD[ms % 32] + time;
    ms = Math.floor(ms / 32);
  }
  idCounter += 1;
  // 4 counter chars keep ids unique within one millisecond; the rest is random.
  let rand = '';
  for (let i = 0, n = idCounter; i < 4; i++, n = Math.floor(n / 32))
    rand = CROCKFORD[n % 32] + rand;
  for (let i = 0; i < 12; i++) rand += CROCKFORD[Math.floor(Math.random() * 32)];
  return time + rand;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n: number) => String(n).padStart(2, '0');

/** `Activity_<Area>_<Summary|Detailed>_<DD-MMM-YYYY>_<HHmm>_UTC_<exportId>.<ext>` */
export function exportFileName(job: ExportJob): string {
  const d = new Date(job.requested_at);
  const area = job.area === 'all' ? 'All_Areas' : job.area[0]?.toUpperCase() + job.area.slice(1);
  const mode = job.mode === 'detailed' ? 'Detailed' : 'Summary';
  const date = `${pad(d.getUTCDate())}-${MONTHS[d.getUTCMonth()]}-${d.getUTCFullYear()}`;
  const time = `${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}`;
  const ext = (job.part_count ?? 1) > 1 ? 'zip' : job.format;
  return `Activity_${area}_${mode}_${date}_${time}_UTC_${job.export_id}.${ext}`;
}

export interface ActivityFilter {
  area?: string | null;
  action?: string | null;
  from?: string | null;
  to?: string | null;
}

export function filterEvents(events: ActivityEvent[], f: ActivityFilter): ActivityEvent[] {
  const from = f.from ? Date.parse(f.from) : -Infinity;
  const to = f.to ? Date.parse(f.to) : Infinity;
  return events.filter((e) => {
    if (f.area && f.area !== 'all' && e.area !== f.area) return false;
    if (f.action && e.action !== f.action) return false;
    const t = Date.parse(e.occurred_at);
    return t >= from && t <= to;
  });
}

export const encodeCursor = (value: unknown) => btoa(JSON.stringify(value));
export function decodeCursor<T>(cursor: string | null): T | null {
  if (!cursor) return null;
  try {
    return JSON.parse(atob(cursor)) as T;
  } catch {
    return null;
  }
}

export interface MockDbOptions {
  /** When set, jobs advance one stage every `stepMs` (dev mode). Tests drive state by hand. */
  autoProgressMs?: number | null;
  seedJobs?: boolean;
}

export class MockDb {
  events: ActivityEvent[] = [];
  jobs: ExportJob[] = [];
  autoProgressMs: number | null = null;
  /** Request log for assertions in tests. */
  calls: { method: string; path: string; body?: unknown }[] = [];

  constructor(options: MockDbOptions = {}) {
    this.reset(options);
  }

  reset({ autoProgressMs = null, seedJobs = false }: MockDbOptions = {}) {
    this.events = seedEvents();
    this.jobs = [];
    this.calls = [];
    this.autoProgressMs = autoProgressMs;
    if (seedJobs) this.seedJobs();
  }

  /** Insert a job with sensible defaults; returns it. */
  addJob(partial: Partial<ExportJob> = {}): ExportJob {
    const requestedAt = partial.requested_at ?? new Date().toISOString();
    const job: ExportJob = {
      export_id: partial.export_id ?? ulid(Date.parse(requestedAt)),
      owner_id: 'demo-user',
      area: 'billing',
      mode: 'summary',
      format: 'csv',
      filters: {},
      status: 'queued',
      requested_at: requestedAt,
      started_at: null,
      completed_at: null,
      row_count: null,
      part_count: null,
      parts_done: null,
      failure_code: null,
      expires_at: Math.floor(Date.parse(requestedAt) / 1000) + RETENTION_S,
      notified_at: null,
      seen_at: null,
      ...partial,
    };
    job.file_name = partial.file_name ?? exportFileName(job);
    this.jobs.unshift(job);
    this.jobs.sort((a, b) => b.requested_at.localeCompare(a.requested_at));
    return job;
  }

  update(id: string, patch: Partial<ExportJob>): ExportJob {
    const job = this.jobs.find((j) => j.export_id === id);
    if (!job) throw new Error(`no job ${id}`);
    Object.assign(job, patch);
    return job;
  }

  createFromRequest(req: ExportRequest): ExportJob {
    const rows = filterEvents(this.events, { area: req.area, ...req.filters }).length;
    // Simulate scale: detailed exports of more than a handful of rows become multi-part.
    const partCount = req.mode === 'detailed' && rows > 50 ? 4 : 1;
    const job = this.addJob({ ...req, filters: req.filters ?? {} });
    // Planned values revealed as the job progresses.
    plans.set(job.export_id, { rows, partCount, createdAt: Date.now() });
    return job;
  }

  /** Lazily advance auto-progressing jobs: queued → running (parts) → completed. */
  tick(now = Date.now()) {
    if (!this.autoProgressMs) return;
    for (const job of this.jobs) {
      const plan = plans.get(job.export_id);
      if (!plan || job.status === 'completed' || job.status === 'failed') continue;
      const steps = Math.floor((now - plan.createdAt) / this.autoProgressMs);
      if (steps < 1) continue;
      if (job.status === 'queued') {
        job.status = 'running';
        job.started_at = new Date(now).toISOString();
        job.high_water_event_id = this.events[0]?.event_id ?? 0;
        job.part_count = plan.partCount;
        job.parts_done = 0;
      }
      const partsDone = Math.min(steps - 1, plan.partCount);
      job.parts_done = plan.partCount > 1 ? partsDone : 0;
      if (steps >= plan.partCount + 2) {
        job.status = 'completed';
        job.parts_done = plan.partCount;
        job.row_count = plan.rows;
        job.completed_at = new Date(now).toISOString();
        job.expires_at = Math.floor(now / 1000) + RETENTION_S;
        job.notified_at = job.completed_at;
        job.file_name = exportFileName(job);
      }
    }
  }

  private seedJobs() {
    const now = Date.now();
    const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
    this.addJob({
      area: 'orders',
      mode: 'detailed',
      format: 'xlsx',
      status: 'completed',
      requested_at: iso(2 * 3600_000),
      started_at: iso(2 * 3600_000 - 2_000),
      completed_at: iso(2 * 3600_000 - 60_000),
      row_count: 18_420,
      part_count: 1,
      parts_done: 1,
      seen_at: iso(3600_000),
    });
    this.addJob({
      area: 'all',
      mode: 'detailed',
      format: 'csv',
      status: 'failed',
      failure_code: 'TOO_LARGE',
      requested_at: iso(26 * 3600_000),
    });
    this.addJob({
      area: 'billing',
      mode: 'summary',
      format: 'csv',
      status: 'completed',
      requested_at: iso(9 * DAY),
      completed_at: iso(9 * DAY - 30_000),
      row_count: 2_114,
      expires_at: Math.floor((now - 2 * DAY) / 1000),
      seen_at: iso(8 * DAY),
    });
  }
}

const plans = new Map<string, { rows: number; partCount: number; createdAt: number }>();

export const db = new MockDb();
