import { setupWorker } from 'msw/browser';
import { db } from './db';
import { handlers } from './handlers';

/** Dev-only mock backend: seeded history, and new jobs advance a stage every 3 s. */
export async function startMockWorker() {
  db.reset({ autoProgressMs: 3_000, seedJobs: true });
  const worker = setupWorker(...handlers);
  await worker.start({ onUnhandledRequest: 'bypass', quiet: false });
  console.info('[mock] MSW active: /api is served from memory (no backend required).');
}
