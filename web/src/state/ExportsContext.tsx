import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { ApiError } from '../api/client';
import { createExport, listExports, markExportsSeen, requestDownload } from '../api/endpoints';
import type { ExportJob, ExportRequest, ExportStatus } from '../api/types';
import { failureLabel, isActive, isFinished, jobTitle } from '../lib/format';
import { navigateTo } from '../lib/navigation';
import { useBackoffPolling } from '../lib/useBackoffPolling';
import { useToast } from './ToastContext';

interface ExportsApi {
  jobs: ExportJob[];
  /** Cursor for the page after `jobs` ("Load more" on the full page). */
  nextCursor: string | null;
  loaded: boolean;
  error: string | null;
  unreadCount: number;
  hasActive: boolean;
  /** Ids the user tried to download and the server answered 410 Gone. */
  expiredIds: ReadonlySet<string>;
  downloadingId: string | null;
  refresh: () => Promise<void>;
  queueExport: (request: ExportRequest) => Promise<ExportJob | null>;
  markSeen: (ids?: string[]) => void;
  download: (job: ExportJob) => Promise<void>;
  trayOpen: boolean;
  setTrayOpen: (open: boolean) => void;
}

const ExportsContext = createContext<ExportsApi | null>(null);

export interface ExportsProviderProps {
  children: ReactNode;
  /** Poll schedule; overridable for tests. Defaults to the contract's 5 s → 30 s. */
  pollMinMs?: number;
  pollMaxMs?: number;
}

const PAGE_SIZE = 20;

function downloadErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 410) return 'This export has expired. Request a new export to get the data.';
    if (err.status === 409) return 'This export is not ready yet. Try again when it completes.';
    if (err.status === 404) return 'This export no longer exists.';
  }
  return 'The download could not be started. Please try again.';
}

export function ExportsProvider({
  children,
  pollMinMs = 5_000,
  pollMaxMs = 30_000,
}: ExportsProviderProps) {
  const toast = useToast();
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expiredIds, setExpiredIds] = useState<ReadonlySet<string>>(() => new Set());
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [trayOpen, setTrayOpen] = useState(false);
  const [pollReset, setPollReset] = useState(0);
  // Last known status per job, used to detect queued/running → completed/failed transitions.
  const lastStatus = useRef<Map<string, ExportStatus> | null>(null);

  const announceTransitions = useCallback(
    (items: ExportJob[]) => {
      const previous = lastStatus.current;
      if (previous) {
        for (const job of items) {
          const before = previous.get(job.export_id);
          if (!before || !isActive({ status: before }) || !isFinished(job)) continue;
          if (job.status === 'completed') {
            toast.push({
              tone: 'success',
              title: 'Export ready',
              description: `${jobTitle(job)} is ready to download from My exports.`,
            });
          } else {
            toast.push({
              tone: 'error',
              title: 'Export failed',
              description: `${jobTitle(job)}: ${failureLabel(job.failure_code)}`,
            });
          }
        }
      }
      const next = new Map(previous ?? []);
      for (const job of items) next.set(job.export_id, job.status);
      lastStatus.current = next;
    },
    [toast],
  );

  // A promise chain rather than async/await: state is only ever set in the callbacks, which keeps
  // the mount effect below free of synchronous setState (react-hooks/set-state-in-effect).
  const refresh = useCallback(
    () =>
      listExports({ limit: PAGE_SIZE })
        .then((page) => {
          announceTransitions(page.items);
          setJobs(page.items);
          setNextCursor(page.next_cursor);
          setError(null);
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : 'Could not load exports.');
        })
        .finally(() => {
          setLoaded(true);
        }),
    [announceTransitions],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const hasActive = useMemo(() => jobs.some(isActive), [jobs]);
  useBackoffPolling(refresh, {
    active: hasActive,
    minMs: pollMinMs,
    maxMs: pollMaxMs,
    resetKey: pollReset,
  });

  const queueExport = useCallback(
    async (request: ExportRequest) => {
      try {
        const job = await createExport(request);
        lastStatus.current?.set(job.export_id, job.status);
        setJobs((all) => [job, ...all.filter((j) => j.export_id !== job.export_id)]);
        setPollReset((n) => n + 1);
        toast.push({
          tone: 'info',
          title: 'Export queued',
          description: `${jobTitle(job)}. We'll let you know when it's ready.`,
        });
        return job;
      } catch (err) {
        toast.push({
          tone: 'error',
          title: 'Export could not be queued',
          description: err instanceof Error ? err.message : undefined,
        });
        return null;
      }
    },
    [toast],
  );

  const markSeen = useCallback(
    (ids?: string[]) => {
      const targets =
        ids ?? jobs.filter((j) => isFinished(j) && !j.seen_at).map((j) => j.export_id);
      if (targets.length === 0) return;
      const now = new Date().toISOString();
      const set = new Set(targets);
      setJobs((all) => all.map((j) => (set.has(j.export_id) ? { ...j, seen_at: now } : j)));
      markExportsSeen(targets).catch(() => {
        // Best effort: the badge reappears on the next refresh if this failed.
      });
    },
    [jobs],
  );

  const download = useCallback(
    async (job: ExportJob) => {
      setDownloadingId(job.export_id);
      try {
        const { url } = await requestDownload(job.export_id);
        navigateTo(url);
      } catch (err) {
        if (err instanceof ApiError && err.status === 410) {
          setExpiredIds((s) => new Set(s).add(job.export_id));
        }
        toast.push({
          tone: 'error',
          title: 'Download unavailable',
          description: downloadErrorMessage(err),
        });
      } finally {
        setDownloadingId(null);
      }
    },
    [toast],
  );

  const unreadCount = useMemo(() => jobs.filter((j) => isFinished(j) && !j.seen_at).length, [jobs]);

  const value = useMemo<ExportsApi>(
    () => ({
      jobs,
      nextCursor,
      loaded,
      error,
      unreadCount,
      hasActive,
      expiredIds,
      downloadingId,
      refresh,
      queueExport,
      markSeen,
      download,
      trayOpen,
      setTrayOpen,
    }),
    [
      jobs,
      nextCursor,
      loaded,
      error,
      unreadCount,
      hasActive,
      expiredIds,
      downloadingId,
      refresh,
      queueExport,
      markSeen,
      download,
      trayOpen,
    ],
  );

  return <ExportsContext.Provider value={value}>{children}</ExportsContext.Provider>;
}

export function useExports(): ExportsApi {
  const ctx = useContext(ExportsContext);
  if (!ctx) throw new Error('useExports must be used inside <ExportsProvider>');
  return ctx;
}
