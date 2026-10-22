import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { db } from '../mocks/db';
import { renderApp } from '../test/render';

const listCalls = () =>
  db.calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/exports?'));
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('background export polling', () => {
  it('toasts on completion (on any page) and stops polling when nothing is active', async () => {
    const job = db.addJob({ area: 'billing', status: 'running', part_count: 3, parts_done: 1 });
    const { user } = renderApp({ route: '/activity', pollMinMs: 20, pollMaxMs: 40 });

    // Polls while running.
    await waitFor(() => expect(listCalls().length).toBeGreaterThanOrEqual(3));

    // Navigate elsewhere inside the app; the provider keeps polling.
    await user.click(screen.getByRole('link', { name: 'My exports' }));
    expect(
      await screen.findByRole('heading', { name: 'My exports', level: 1 }),
    ).toBeInTheDocument();

    db.update(job.export_id, { status: 'completed', parts_done: 3, row_count: 900 });

    const status = screen.getByRole('status', { name: 'Notifications' });
    expect(await within(status).findByText('Export ready')).toBeInTheDocument();
    expect(within(status).getByText(/Billing · Summary · CSV is ready/)).toBeInTheDocument();

    // No active jobs → no more polls.
    const settled = listCalls().length;
    await sleep(200);
    expect(listCalls().length).toBe(settled);
  });

  it('announces failures in the assertive region', async () => {
    const job = db.addJob({ area: 'orders', status: 'queued' });
    renderApp();
    await waitFor(() => expect(listCalls().length).toBeGreaterThanOrEqual(2));
    db.update(job.export_id, { status: 'failed', failure_code: 'WORKER_ERROR' });
    const alert = screen.getByRole('alert', { name: 'Errors' });
    expect(await within(alert).findByText('Export failed')).toBeInTheDocument();
  });

  it('does not poll at all when no job is active, and does not toast historical jobs', async () => {
    db.addJob({ status: 'completed', row_count: 1 });
    renderApp();
    await waitFor(() => expect(listCalls()).toHaveLength(1));
    await sleep(150);
    expect(listCalls()).toHaveLength(1);
    expect(screen.queryByText('Export ready')).not.toBeInTheDocument();
  });

  it('starts polling after a new export is queued', async () => {
    const { user } = renderApp();
    await screen.findByText(/Showing 100 events/);
    await user.click(screen.getByRole('button', { name: /^export/i }));
    await user.click(screen.getByRole('menuitem', { name: /CSV – Summary/ }));
    await screen.findByText('Export queued');
    const id = db.jobs[0]!.export_id;
    const before = listCalls().length;
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(before));
    db.update(id, { status: 'completed', row_count: 10 });
    expect(await screen.findByText('Export ready')).toBeInTheDocument();
  });
});
