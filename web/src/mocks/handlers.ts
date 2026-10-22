import { http, HttpResponse } from 'msw';
import { ACTIONS, AREAS } from '../api/types';
import type { ActivityEvent, ExportJob, ExportRequest, Page } from '../api/types';
import { db, decodeCursor, encodeCursor, filterEvents } from './db';

// `*/api/...` matches any origin (dev server, jsdom, VITE_API_BASE).
const API = '*/api';

const detail = (status: number, message: string, failure_code?: string) =>
  HttpResponse.json(failure_code ? { detail: message, failure_code } : { detail: message }, {
    status,
  });

const clampLimit = (raw: string | null, fallback: number, max: number) => {
  const n = Number(raw ?? fallback);
  return Number.isInteger(n) && n >= 1 && n <= max ? n : null;
};

const REQUEST_KEYS = new Set(['area', 'mode', 'format', 'filters']);
const FILTER_KEYS = new Set(['from', 'to', 'action']);

function validateExportRequest(body: unknown): ExportRequest | string {
  if (!body || typeof body !== 'object') return 'Body must be an object';
  const b = body as Record<string, unknown>;
  const unknownKey = Object.keys(b).find((k) => !REQUEST_KEYS.has(k));
  if (unknownKey) return `Unknown field: ${unknownKey}`;
  if (b.area !== 'all' && !AREAS.includes(b.area as never)) return 'Invalid area';
  if (b.mode !== 'summary' && b.mode !== 'detailed') return 'Invalid mode';
  if (b.format !== 'csv' && b.format !== 'xlsx') return 'Invalid format';
  const filters = (b.filters ?? {}) as Record<string, unknown>;
  const unknownFilter = Object.keys(filters).find((k) => !FILTER_KEYS.has(k));
  if (unknownFilter) return `Unknown filter: ${unknownFilter}`;
  if (filters.action !== undefined && !ACTIONS.includes(filters.action as never))
    return 'Invalid action';
  return b as unknown as ExportRequest;
}

function fakeDownloadUrl(job: ExportJob): string {
  const name = job.file_name ?? `${job.export_id}.${job.format}`;
  const csv = 'event_id,occurred_at,area,action\n1,2026-01-01T00:00:00Z,billing,update\n';
  if (typeof URL.createObjectURL === 'function' && typeof window !== 'undefined') {
    try {
      return URL.createObjectURL(new File([csv], name, { type: 'text/csv' }));
    } catch {
      // fall through
    }
  }
  return `https://downloads.example.test/${encodeURIComponent(name)}?X-Amz-Expires=60`;
}

export const handlers = [
  http.get(`${API}/health`, () => HttpResponse.json({ status: 'ok', mock: true })),

  http.get(`${API}/activity`, ({ request }) => {
    const url = new URL(request.url);
    db.calls.push({ method: 'GET', path: `/api/activity${url.search}` });
    const limit = clampLimit(url.searchParams.get('limit'), 100, 500);
    if (limit === null) return detail(422, 'limit must be between 1 and 500');
    const rows = filterEvents(db.events, {
      area: url.searchParams.get('area'),
      action: url.searchParams.get('action'),
      from: url.searchParams.get('from'),
      to: url.searchParams.get('to'),
    });
    const after = decodeCursor<{ occurred_at: string; event_id: number }>(
      url.searchParams.get('cursor'),
    );
    const start = after
      ? rows.findIndex(
          (e) =>
            e.occurred_at < after.occurred_at ||
            (e.occurred_at === after.occurred_at && e.event_id < after.event_id),
        )
      : 0;
    const items = start < 0 ? [] : rows.slice(start, start + limit);
    const last = items[items.length - 1];
    const more = start >= 0 && start + limit < rows.length;
    const body: Page<ActivityEvent> = {
      items,
      next_cursor:
        more && last
          ? encodeCursor({ occurred_at: last.occurred_at, event_id: last.event_id })
          : null,
    };
    return HttpResponse.json(body);
  }),

  http.post(`${API}/exports/seen`, async ({ request }) => {
    const body = (await request.json()) as { ids?: unknown };
    db.calls.push({ method: 'POST', path: '/api/exports/seen', body });
    if (!Array.isArray(body?.ids)) return detail(422, 'ids must be an array');
    const now = new Date().toISOString();
    for (const job of db.jobs) if (body.ids.includes(job.export_id)) job.seen_at ??= now;
    return HttpResponse.json({ updated: body.ids.length });
  }),

  http.post(`${API}/exports`, async ({ request }) => {
    const body: unknown = await request.json().catch(() => null);
    db.calls.push({ method: 'POST', path: '/api/exports', body });
    const parsed = validateExportRequest(body);
    if (typeof parsed === 'string') return detail(422, parsed);
    const job = db.createFromRequest(parsed);
    return HttpResponse.json(job, {
      status: 202,
      headers: { Location: `/api/exports/${job.export_id}` },
    });
  }),

  http.get(`${API}/exports`, ({ request }) => {
    const url = new URL(request.url);
    db.calls.push({ method: 'GET', path: `/api/exports${url.search}` });
    const limit = clampLimit(url.searchParams.get('limit'), 20, 50);
    if (limit === null) return detail(422, 'limit must be between 1 and 50');
    db.tick();
    const offset = decodeCursor<{ offset: number }>(url.searchParams.get('cursor'))?.offset ?? 0;
    const items = db.jobs.slice(offset, offset + limit).map((j) => ({ ...j }));
    const body: Page<ExportJob> = {
      items,
      next_cursor:
        offset + limit < db.jobs.length ? encodeCursor({ offset: offset + limit }) : null,
    };
    return HttpResponse.json(body);
  }),

  http.get(`${API}/exports/:id`, ({ params }) => {
    db.calls.push({ method: 'GET', path: `/api/exports/${String(params.id)}` });
    db.tick();
    const job = db.jobs.find((j) => j.export_id === params.id);
    return job ? HttpResponse.json(job) : detail(404, 'Export not found');
  }),

  http.post(`${API}/exports/:id/download`, ({ params }) => {
    db.calls.push({ method: 'POST', path: `/api/exports/${String(params.id)}/download` });
    const job = db.jobs.find((j) => j.export_id === params.id);
    if (!job) return detail(404, 'Export not found');
    if (job.status !== 'completed') return detail(409, 'Export not completed', 'NOT_READY');
    if (typeof job.expires_at === 'number' && job.expires_at * 1000 <= Date.now()) {
      return detail(410, 'Export expired', 'EXPIRED');
    }
    return HttpResponse.json({ url: fakeDownloadUrl(job), file_name: job.file_name });
  }),
];
