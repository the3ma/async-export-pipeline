/** Thin typed wrapper around fetch. All API paths start with /api. */

const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';

export class ApiError extends Error {
  readonly status: number;
  readonly failureCode: string | null;

  constructor(status: number, message: string, failureCode: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.failureCode = failureCode;
  }
}

type QueryValue = string | number | null | undefined;

export interface RequestOptions {
  method?: 'GET' | 'POST';
  query?: Record<string, QueryValue>;
  body?: unknown;
  signal?: AbortSignal;
}

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  // Resolve against the page origin so relative bases ("" or "/") work in every runtime.
  const url = new URL(`${API_BASE.replace(/\/$/, '')}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function readError(res: Response): Promise<ApiError> {
  let message = res.statusText || `Request failed (${res.status})`;
  let failureCode: string | null = null;
  try {
    const body: unknown = await res.json();
    if (body && typeof body === 'object') {
      const { detail, failure_code } = body as { detail?: unknown; failure_code?: unknown };
      if (typeof detail === 'string') message = detail;
      if (typeof failure_code === 'string') failureCode = failure_code;
    }
  } catch {
    // Non-JSON error body: keep the status text.
  }
  return new ApiError(res.status, message, failureCode);
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', query, body, signal } = options;
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const res = await fetch(buildUrl(path, query), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
    credentials: 'same-origin',
  });
  if (!res.ok) throw await readError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
