import { Link, Navigate, Route, Routes } from 'react-router-dom';
import { AppHeader } from './components/AppHeader';
import { ActivityPage } from './pages/ActivityPage';
import { ExportsPage } from './pages/ExportsPage';
import { ExportsProvider } from './state/ExportsContext';
import type { ExportsProviderProps } from './state/ExportsContext';
import { ToastProvider } from './state/ToastContext';

/** Opt in to React Router v7 behaviour now (silences the v6 deprecation warnings). */
export const ROUTER_FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true } as const;

type Props = Pick<ExportsProviderProps, 'pollMinMs' | 'pollMaxMs'>;

/** Providers sit above the routes so polling and toasts survive navigation. */
export function App(props: Props) {
  return (
    <ToastProvider>
      <ExportsProvider {...props}>
        <a href="#main" className="skip-link">
          Skip to main content
        </a>
        <AppHeader />
        <main id="main" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<Navigate to="/activity" replace />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/exports" element={<ExportsPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </ExportsProvider>
    </ToastProvider>
  );
}

function NotFound() {
  return (
    <div className="page">
      <h1>Page not found</h1>
      <p>
        <Link to="/activity">Go to Activity</Link>
      </p>
    </div>
  );
}
