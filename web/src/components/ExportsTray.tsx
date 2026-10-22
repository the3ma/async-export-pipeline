import { useEffect, useRef } from 'react';
import type { KeyboardEvent, RefObject } from 'react';
import { Link } from 'react-router-dom';
import { useExports } from '../state/ExportsContext';
import { ExportsList } from './ExportsList';
import styles from './ExportsTray.module.css';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Slide-over modal dialog listing the user's recent exports. */
export function ExportsTray({ returnFocusTo }: { returnFocusTo: RefObject<HTMLElement> }) {
  const { trayOpen, setTrayOpen, jobs, loaded, error, markSeen } = useExports();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Mark every unread item as seen when the tray opens (and when items finish while it is open).
  const markSeenRef = useRef(markSeen);
  useEffect(() => {
    markSeenRef.current = markSeen;
  }, [markSeen]);
  const unreadKey = jobs
    .filter((j) => !j.seen_at && (j.status === 'completed' || j.status === 'failed'))
    .map((j) => j.export_id)
    .join(',');
  useEffect(() => {
    if (trayOpen && loaded && unreadKey) markSeenRef.current();
  }, [trayOpen, loaded, unreadKey]);

  useEffect(() => {
    if (!trayOpen) return;
    const returnTarget = returnFocusTo.current;
    closeRef.current?.focus();
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = overflow;
      returnTarget?.focus();
    };
  }, [trayOpen, returnFocusTo]);

  if (!trayOpen) return null;

  const close = () => setTrayOpen(false);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      close();
      return;
    }
    if (e.key !== 'Tab' || !panelRef.current) return;
    const nodes = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (!first || !last) return;
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  return (
    <div className={styles.overlay} onKeyDown={onKeyDown}>
      <div className={styles.backdrop} onClick={close} aria-hidden="true" />
      <div
        ref={panelRef}
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby="exports-tray-title"
      >
        <header className={styles.header}>
          <h2 id="exports-tray-title">My exports</h2>
          <button
            ref={closeRef}
            type="button"
            className={styles.close}
            onClick={close}
            aria-label="Close my exports"
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>
        <div className={styles.body}>
          {!loaded && <p className="muted">Loading exports…</p>}
          {loaded && error && jobs.length === 0 && (
            <p className="field-error">Could not load exports. {error}</p>
          )}
          {loaded && !error && jobs.length === 0 && (
            <p className="muted">No exports yet. Use Export on the Activity page to create one.</p>
          )}
          {jobs.length > 0 && <ExportsList jobs={jobs} compact />}
        </div>
        <footer className={styles.footer}>
          <Link to="/exports" onClick={close}>
            View all exports
          </Link>
        </footer>
      </div>
    </div>
  );
}
