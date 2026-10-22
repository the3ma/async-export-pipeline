// Types mirror docs/CONTRACT.md. Keep them in sync with the API.

export const AREAS = ['accounts', 'billing', 'catalog', 'orders', 'settings'] as const;
export type Area = (typeof AREAS)[number];
export type ExportArea = Area | 'all';

export const ACTIONS = ['create', 'update', 'delete', 'view', 'export', 'sign_in'] as const;
export type Action = (typeof ACTIONS)[number];

export type ExportMode = 'summary' | 'detailed';
export type ExportFormat = 'csv' | 'xlsx';
export type ExportStatus = 'queued' | 'running' | 'completed' | 'failed';
export type FailureCode =
  'QUEUE_SEND_FAILED' | 'TOO_LARGE' | 'ROWSET_CHANGED' | 'WORKER_ERROR' | 'EXPIRED';

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface ActivityEvent {
  event_id: number;
  occurred_at: string;
  actor_id: string;
  area: Area;
  action: Action;
  entity_type: string;
  entity_id: string;
}

export interface ActivityQuery {
  area?: Area;
  action?: Action;
  /** ISO-8601 */
  from?: string;
  /** ISO-8601 */
  to?: string;
  limit?: number;
  cursor?: string | null;
}

export interface ExportFilters {
  from?: string;
  to?: string;
  action?: Action;
}

export interface ExportRequest {
  area: ExportArea;
  mode: ExportMode;
  format: ExportFormat;
  filters: ExportFilters;
}

export interface ExportJob {
  export_id: string;
  owner_id: string;
  area: ExportArea;
  mode: ExportMode;
  format: ExportFormat;
  filters: ExportFilters;
  status: ExportStatus;
  requested_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  high_water_event_id?: number | null;
  row_count?: number | null;
  part_count?: number | null;
  parts_done?: number | null;
  file_name?: string | null;
  failure_code?: FailureCode | null;
  /** epoch seconds */
  expires_at?: number | null;
  notified_at?: string | null;
  seen_at?: string | null;
}

export interface DownloadResponse {
  url: string;
  file_name: string;
}
