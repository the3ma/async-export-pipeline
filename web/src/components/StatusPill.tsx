import type { ExportStatus } from '../api/types';
import styles from './StatusPill.module.css';

const LABELS: Record<ExportStatus, string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
};

export function StatusPill({
  status,
  expired = false,
}: {
  status: ExportStatus;
  expired?: boolean;
}) {
  const label = expired && status === 'completed' ? 'Expired' : LABELS[status];
  const tone = expired && status === 'completed' ? 'queued' : status;
  return <span className={`${styles.pill} ${styles[tone]}`}>{label}</span>;
}
