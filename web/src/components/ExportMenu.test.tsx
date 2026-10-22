import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { db } from '../mocks/db';
import { renderApp } from '../test/render';

describe('Export menu', () => {
  it('queues an export with the current filters and shows a toast', async () => {
    const { user } = renderApp();
    await screen.findByText(/Showing 100 events/);

    await user.selectOptions(screen.getByLabelText('Area'), 'billing');
    await user.selectOptions(screen.getByLabelText('Action'), 'update');
    await user.type(screen.getByLabelText('From (UTC)'), '2026-01-01');

    const button = screen.getByRole('button', { name: /^export/i, expanded: false });
    await user.click(button);
    const menu = screen.getByRole('menu', { name: 'Export format' });
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(4);
    await user.click(within(menu).getByRole('menuitem', { name: /Excel \(XLSX\) – Detailed/ }));

    const status = screen.getByRole('status', { name: 'Notifications' });
    expect(await within(status).findByText('Export queued')).toBeInTheDocument();

    const post = db.calls.find((c) => c.method === 'POST' && c.path === '/api/exports');
    expect(post?.body).toEqual({
      area: 'billing',
      mode: 'detailed',
      format: 'xlsx',
      filters: { action: 'update', from: '2026-01-01T00:00:00Z' },
    });
    expect(db.jobs).toHaveLength(1);
    expect(db.jobs[0]?.status).toBe('queued');
  });

  it('is keyboard operable: arrows move, Escape closes and restores focus', async () => {
    const { user } = renderApp();
    await screen.findByText(/Showing 100 events/);
    const button = screen.getByRole('button', { name: /^export/i });

    button.focus();
    await user.keyboard('{ArrowDown}');
    const items = screen.getAllByRole('menuitem');
    expect(items[0]).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(items[1]).toHaveFocus();
    await user.keyboard('{End}');
    expect(items[3]).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(items[0]).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(button).toHaveFocus();

    // Enter on an item queues the export (area defaults to "all").
    await user.keyboard('{Enter}');
    await user.keyboard('{Enter}');
    await waitFor(() => expect(db.jobs).toHaveLength(1));
    expect(db.jobs[0]).toMatchObject({ area: 'all', mode: 'summary', format: 'csv' });
  });
});
