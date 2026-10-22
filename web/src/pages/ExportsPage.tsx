import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router';
import { listExports } from '../api/endpoints';
import type { ExportJob } from '../api/types';
import { ExportsList } from '../components/ExportsList';
import { useExports } from '../state/ExportsContext';

export function ExportsPage() {
  const { jobs, nextCursor, loaded, error, markSeen, refresh } = useExports();
  const [older, setOlder] = useState<ExportJob[]>([]);
  const [olderCursor, setOlderCursor] = useState<string | null | undefined>(undefined);
  const [loadingMore, setLoadingMore] = useState(false);

  // Viewing the full list counts as seeing the items, same as opening the tray.
  const markSeenRef = useRef(markSeen);
  useEffect(() => {
    markSeenRef.current = markSeen;
  }, [markSeen]);
  const unreadKey = jobs
    .filter((j) => !j.seen_at && (j.status === 'completed' || j.status === 'failed'))
    .map((j) => j.export_id)
    .join(',');
  useEffect(() => {
    if (loaded && unreadKey) markSeenRef.current();
  }, [loaded, unreadKey]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const cursor = olderCursor === undefined ? nextCursor : olderCursor;

  const all = useMemo(() => {
    const seen = new Set(jobs.map((j) => j.export_id));
    return [...jobs, ...older.filter((j) => !seen.has(j.export_id))];
  }, [jobs, older]);

  const loadMore = async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await listExports({ limit: 20, cursor });
      setOlder((o) => [...o, ...page.items]);
      setOlderCursor(page.next_cursor);
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>My exports</h1>
          <p>
            Files are kept for 7 days after they are generated. You will also get an e-mail when an
            export finishes.
          </p>
        </div>
        <Link to="/activity" className="btn">
          New export
        </Link>
      </div>

      {!loaded && <p className="muted">Loading exports…</p>}
      {loaded && error && all.length === 0 && (
        <p className="field-error" role="alert">
          Could not load exports. {error}
        </p>
      )}
      {loaded && !error && all.length === 0 && (
        <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
          <p className="muted">
            No exports yet. Go to <Link to="/activity">Activity</Link> and choose Export.
          </p>
        </div>
      )}
      {all.length > 0 && <ExportsList jobs={all} />}

      {cursor && (
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '1rem' }}>
          <button
            type="button"
            className="btn"
            onClick={() => void loadMore()}
            disabled={loadingMore}
          >
            {loadingMore ? 'Loading…' : 'Load older exports'}
          </button>
        </div>
      )}
    </div>
  );
}
