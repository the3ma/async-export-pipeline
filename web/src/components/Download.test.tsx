import { screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { navigateTo } from '../lib/navigation';
import { db } from '../mocks/db';
import { server } from '../mocks/server';
import { renderApp } from '../test/render';

vi.mock('../lib/navigation', () => ({ navigateTo: vi.fn() }));
const nav = vi.mocked(navigateTo);

const nowS = () => Math.floor(Date.now() / 1000);

describe('Download', () => {
  it('requests a presigned URL and navigates to it', async () => {
    const job = db.addJob({ status: 'completed', row_count: 5, seen_at: new Date().toISOString() });
    const { user } = renderApp({ route: '/exports' });

    await user.click(await screen.findByRole('button', { name: 'Download' }));
    await waitFor(() => expect(nav).toHaveBeenCalledTimes(1));
    expect(nav.mock.calls[0]?.[0]).toMatch(/^(blob:|https:\/\/downloads\.example\.test\/)/);
    expect(db.calls).toContainEqual({
      method: 'POST',
      path: `/api/exports/${job.export_id}/download`,
    });
  });

  it('explains a 410 Gone and stops offering the download', async () => {
    // Client clock says still valid; the server says expired (it is the source of truth).
    const job = db.addJob({ status: 'completed', row_count: 5, expires_at: nowS() + 3600 });
    server.use(
      http.post('*/api/exports/:id/download', () =>
        HttpResponse.json({ detail: 'Export expired', failure_code: 'EXPIRED' }, { status: 410 }),
      ),
    );
    const { user } = renderApp({ route: '/exports' });

    const card = await screen.findByRole('article', { name: /Billing · Summary · CSV/ });
    await user.click(within(card).getByRole('button', { name: 'Download' }));

    const alert = screen.getByRole('alert', { name: 'Errors' });
    expect(await within(alert).findByText('Download unavailable')).toBeInTheDocument();
    expect(within(alert).getByText(/This export has expired/)).toBeInTheDocument();
    expect(nav).not.toHaveBeenCalled();
    expect(within(card).queryByRole('button', { name: 'Download' })).not.toBeInTheDocument();
    expect(within(card).getByText('Expired')).toBeInTheDocument();
    expect(job.status).toBe('completed');
  });

  it('explains a 409 when the export is not ready', async () => {
    db.addJob({ status: 'completed', row_count: 5 });
    server.use(
      http.post('*/api/exports/:id/download', () =>
        HttpResponse.json(
          { detail: 'Export not completed', failure_code: 'NOT_READY' },
          { status: 409 },
        ),
      ),
    );
    const { user } = renderApp({ route: '/exports' });
    await user.click(await screen.findByRole('button', { name: 'Download' }));
    expect(await screen.findByText(/not ready yet/)).toBeInTheDocument();
  });

  it('shows already-expired exports without a download button', async () => {
    db.addJob({ status: 'completed', row_count: 5, expires_at: nowS() - 60 });
    renderApp({ route: '/exports' });
    const card = await screen.findByRole('article', { name: /Billing/ });
    expect(within(card).getByText('Expired')).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Download' })).not.toBeInTheDocument();
  });
});
