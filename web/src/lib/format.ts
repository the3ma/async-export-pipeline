import type {
  Action,
  ExportArea,
  ExportFormat,
  ExportJob,
  ExportMode,
  FailureCode,
} from '../api/types';

const AREA_LABELS: Record<ExportArea, string> = {
  all: 'All areas',
  accounts: 'Accounts',
  billing: 'Billing',
  catalog: 'Catalog',
  orders: 'Orders',
  settings: 'Settings',
};

const ACTION_LABELS: Record<Action, string> = {
  create: 'Create',
  update: 'Update',
  delete: 'Delete',
  view: 'View',
  export: 'Export',
  sign_in: 'Sign in',
};

const FAILURE_LABELS: Record<FailureCode, string> = {
  QUEUE_SEND_FAILED: 'The export could not be queued. Please try again.',
  TOO_LARGE: 'Too many rows. Narrow the filters and try again.',
  ROWSET_CHANGED: 'The data changed while exporting. Please try again.',
  WORKER_ERROR: 'Something went wrong while generating the file.',
  EXPIRED: 'This export has expired.',
};

export const areaLabel = (area: ExportArea) => AREA_LABELS[area] ?? area;
export const actionLabel = (action: Action) => ACTION_LABELS[action] ?? action;
export const modeLabel = (mode: ExportMode) => (mode === 'detailed' ? 'Detailed' : 'Summary');
export const formatLabel = (format: ExportFormat) => format.toUpperCase();
export const failureLabel = (code: FailureCode | null | undefined) =>
  (code && FAILURE_LABELS[code]) || 'The export failed.';

export const jobTitle = (job: Pick<ExportJob, 'area' | 'mode' | 'format'>) =>
  `${areaLabel(job.area)} · ${modeLabel(job.mode)} · ${formatLabel(job.format)}`;

const dateTime = new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short' });

export function formatDateTime(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : dateTime.format(date);
}

const integer = new Intl.NumberFormat('en-US');
export const formatCount = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : integer.format(n);

export const isActive = (job: Pick<ExportJob, 'status'>) =>
  job.status === 'queued' || job.status === 'running';
export const isFinished = (job: Pick<ExportJob, 'status'>) =>
  job.status === 'completed' || job.status === 'failed';
export const isExpired = (job: Pick<ExportJob, 'expires_at'>, now = Date.now()) =>
  typeof job.expires_at === 'number' && job.expires_at * 1000 <= now;
