import type { ExportJob } from '../api/types';
import { failureLabel, formatCount, formatDateTime, isExpired, jobTitle } from '../lib/format';
import { useExports } from '../state/ExportsContext';
import { StatusPill } from './StatusPill';
import styles from './ExportsList.module.css';

interface Props {
  jobs: ExportJob[];
  compact?: boolean;
}

export function ExportsList({ jobs, compact = false }: Props) {
  return (
    <ul className={`${styles.list} ${compact ? styles.compact : ''}`}>
      {jobs.map((job) => (
        <li key={job.export_id}>
          <ExportCard job={job} />
        </li>
      ))}
    </ul>
  );
}

function Progress({ job }: { job: ExportJob }) {
  if (job.status === 'queued') return <span className="muted">Waiting in queue</span>;
  if (job.status !== 'running') return null;
  const total = job.part_count ?? 0;
  if (total <= 1) return <span className="muted">Generating file…</span>;
  const done = Math.min(job.parts_done ?? 0, total);
  return (
    <span className={styles.progress}>
      <progress value={done} max={total} aria-label={`Parts exported: ${done} of ${total}`} />
      <span>
        {done} of {total} parts
      </span>
    </span>
  );
}

function ExportCard({ job }: { job: ExportJob }) {
  const { download, downloadingId, expiredIds } = useExports();
  const expired = expiredIds.has(job.export_id) || (job.status === 'completed' && isExpired(job));
  const titleId = `export-${job.export_id}-title`;
  const unread = !job.seen_at && (job.status === 'completed' || job.status === 'failed');

  return (
    <article className={styles.card} aria-labelledby={titleId}>
      <header className={styles.header}>
        <h3 id={titleId} className={styles.title}>
          {jobTitle(job)}
          {unread && (
            <span className={styles.newDot}>
              <span className="visually-hidden"> (new)</span>
            </span>
          )}
        </h3>
        <StatusPill status={job.status} expired={expired} />
      </header>

      <dl className={styles.meta}>
        <div>
          <dt>Rows</dt>
          <dd>{formatCount(job.row_count)}</dd>
        </div>
        <div>
          <dt>Requested</dt>
          <dd>{formatDateTime(job.requested_at)}</dd>
        </div>
        <div>
          <dt>Expires</dt>
          <dd>{formatDateTime(job.expires_at)}</dd>
        </div>
      </dl>

      <div className={styles.footer}>
        <Progress job={job} />
        {job.status === 'failed' && (
          <p className={styles.failure}>{failureLabel(job.failure_code)}</p>
        )}
        {job.status === 'completed' && expired && (
          <p className="muted">Expired. Request a new export to get this data.</p>
        )}
        {job.status === 'completed' && !expired && (
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => void download(job)}
            disabled={downloadingId === job.export_id}
            aria-describedby={titleId}
          >
            {downloadingId === job.export_id ? 'Preparing…' : 'Download'}
          </button>
        )}
      </div>
    </article>
  );
}
