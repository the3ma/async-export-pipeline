import { Link, Navigate, Route, Routes } from 'react-router';
import { AppHeader } from './components/AppHeader';
import { ActivityPage } from './pages/ActivityPage';
import { ExportsPage } from './pages/ExportsPage';
import { ExportsProvider } from './state/ExportsContext';
import type { ExportsProviderProps } from './state/ExportsContext';
import { ToastProvider } from './state/ToastContext';

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
