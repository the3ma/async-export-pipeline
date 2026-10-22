import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { db } from '../mocks/db';
import { renderApp } from '../test/render';

function seed() {
  const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
  const queued = db.addJob({ area: 'catalog', status: 'queued', requested_at: ago(1) });
  const running = db.addJob({
    area: 'orders',
    mode: 'detailed',
    format: 'xlsx',
    status: 'running',
    part_count: 5,
    parts_done: 2,
    requested_at: ago(5),
  });
  const completed = db.addJob({
    area: 'billing',
    status: 'completed',
    row_count: 12_345,
    requested_at: ago(30),
  });
  const failed = db.addJob({
    area: 'all',
    status: 'failed',
    failure_code: 'TOO_LARGE',
    requested_at: ago(60),
  });
  return { queued, running, completed, failed };
}

describe('Exports tray', () => {
  it('shows statuses and multi-part progress, and marks finished items seen on open', async () => {
    const { completed, failed } = seed();
    const { user } = renderApp({ pollMinMs: 60_000, pollMaxMs: 60_000 });

    const trayButton = await screen.findByRole('button', { name: /open my exports, 2 unread/i });
    expect(trayButton).toHaveAccessibleName('Open my exports, 2 unread, exports in progress');
    await user.click(trayButton);

    const dialog = screen.getByRole('dialog', { name: 'My exports' });
    expect(screen.getByRole('button', { name: 'Close my exports' })).toHaveFocus();

    const card = (title: string) =>
      within(dialog).getByRole('article', { name: new RegExp(`^${title}`) });

    expect(within(card('Catalog · Summary · CSV')).getByText('Queued')).toBeInTheDocument();
    expect(
      within(card('Catalog · Summary · CSV')).getByText('Waiting in queue'),
    ).toBeInTheDocument();

    const running = card('Orders · Detailed · XLSX');
    expect(within(running).getByText('Running')).toBeInTheDocument();
    expect(within(running).getByText('2 of 5 parts')).toBeInTheDocument();
    const bar = within(running).getByRole('progressbar', { name: 'Parts exported: 2 of 5' });
    expect(bar).toHaveAttribute('value', '2');
    expect(bar).toHaveAttribute('max', '5');

    const done = card('Billing · Summary · CSV');
    expect(within(done).getByText('Completed')).toBeInTheDocument();
    expect(within(done).getByText('12,345')).toBeInTheDocument();
    expect(within(done).getByRole('button', { name: 'Download' })).toBeEnabled();

    const bad = card('All areas · Summary · CSV');
    expect(within(bad).getByText('Failed')).toBeInTheDocument();
    expect(within(bad).getByText(/Too many rows/)).toBeInTheDocument();
    expect(within(bad).queryByRole('button', { name: 'Download' })).not.toBeInTheDocument();

    await waitFor(() => {
      const seen = db.calls.find((c) => c.path === '/api/exports/seen');
      expect(seen?.body).toEqual({
        ids: expect.arrayContaining([completed.export_id, failed.export_id]),
      });
    });
    expect(trayButton).toHaveAccessibleName('Open my exports, exports in progress');

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trayButton).toHaveFocus();
  });

  it('shows an empty state when there are no exports', async () => {
    const { user } = renderApp();
    await user.click(await screen.findByRole('button', { name: 'Open my exports' }));
    expect(within(screen.getByRole('dialog')).getByText(/No exports yet/)).toBeInTheDocument();
  });
});
