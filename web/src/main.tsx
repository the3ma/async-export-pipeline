import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router';
import { App } from './App';
import './styles/global.css';

async function enableMocks() {
  // `npm run dev:mock` only; this branch is removed from production builds.
  if (import.meta.env.MODE !== 'mock') return;
  const { startMockWorker } = await import('./mocks/browser');
  await startMockWorker();
}

const root = document.getElementById('root');
if (!root) throw new Error('#root element missing');

void enableMocks().then(() => {
  createRoot(root).render(
    <StrictMode>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </StrictMode>,
  );
});
