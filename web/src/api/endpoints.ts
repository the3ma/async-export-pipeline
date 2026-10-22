import { apiRequest } from './client';
import type {
  ActivityEvent,
  ActivityQuery,
  DownloadResponse,
  ExportJob,
  ExportRequest,
  Page,
} from './types';

export function listActivity(query: ActivityQuery, signal?: AbortSignal) {
  const { cursor, ...rest } = query;
  return apiRequest<Page<ActivityEvent>>('/api/activity', {
    query: { ...rest, cursor },
    signal,
  });
}

export function createExport(request: ExportRequest) {
  return apiRequest<ExportJob>('/api/exports', { method: 'POST', body: request });
}

export function listExports(params: { limit?: number; cursor?: string | null } = {}) {
  return apiRequest<Page<ExportJob>>('/api/exports', {
    query: { limit: params.limit ?? 20, cursor: params.cursor },
  });
}

export function getExport(id: string) {
  return apiRequest<ExportJob>(`/api/exports/${encodeURIComponent(id)}`);
}

export function markExportsSeen(ids: string[]) {
  return apiRequest<unknown>('/api/exports/seen', { method: 'POST', body: { ids } });
}

export function requestDownload(id: string) {
  return apiRequest<DownloadResponse>(`/api/exports/${encodeURIComponent(id)}/download`, {
    method: 'POST',
  });
}
