import { useRef } from 'react';
import { NavLink } from 'react-router';
import { useExports } from '../state/ExportsContext';
import { ExportsTray } from './ExportsTray';
import styles from './AppHeader.module.css';

export function AppHeader() {
  const { unreadCount, hasActive, trayOpen, setTrayOpen } = useExports();
  const trayButtonRef = useRef<HTMLButtonElement>(null);

  const trayLabel = [
    'Open my exports',
    unreadCount > 0 ? `${unreadCount} unread` : null,
    hasActive ? 'exports in progress' : null,
  ]
    .filter(Boolean)
    .join(', ');

  const navClass = ({ isActive }: { isActive: boolean }) => (isActive ? styles.active : undefined);

  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <NavLink to="/activity" className={styles.brand}>
          <span aria-hidden="true" className={styles.logo} />
          Activity Log
        </NavLink>
        <nav aria-label="Main" className={styles.nav}>
          <NavLink to="/activity" className={navClass}>
            Activity
          </NavLink>
          <NavLink to="/exports" className={navClass}>
            My exports
          </NavLink>
        </nav>
        <button
          ref={trayButtonRef}
          type="button"
          className={`btn ${styles.trayButton}`}
          aria-label={trayLabel}
          aria-haspopup="dialog"
          aria-expanded={trayOpen}
          onClick={() => setTrayOpen(true)}
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" fill="none">
            <path
              d="M12 4v10m0 0-4-4m4 4 4-4M5 19h14"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className={styles.trayText}>Exports</span>
          {hasActive && <span className={styles.spinner} aria-hidden="true" />}
          {unreadCount > 0 && (
            <span className={styles.badge} aria-hidden="true">
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          )}
        </button>
      </div>
      <ExportsTray returnFocusTo={trayButtonRef} />
    </header>
  );
}
