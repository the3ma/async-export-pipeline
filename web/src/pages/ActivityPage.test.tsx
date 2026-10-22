import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { db } from '../mocks/db';
import { renderApp } from '../test/render';

const activityCalls = () => db.calls.filter((c) => c.path.startsWith('/api/activity'));

describe('Activity page', () => {
  it('loads the first keyset page and appends the next one on "Load more"', async () => {
    const { user } = renderApp();
    expect(await screen.findByText('Showing 100 events (more available).')).toBeInTheDocument();
    const table = screen.getByRole('table', { name: /Activity events/ });
    expect(within(table).getAllByRole('row')).toHaveLength(101); // header + 100

    await user.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Showing 200 events (more available).')).toBeInTheDocument();
    expect(activityCalls()[1]?.path).toMatch(/cursor=/);

    const ids = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((r) => r.querySelectorAll('td')[4]?.textContent);
    expect(ids).toHaveLength(200);
  });

  it('sends filters as query params and validates the date range', async () => {
    const { user } = renderApp();
    await screen.findByText(/Showing 100 events/);

    await user.selectOptions(screen.getByLabelText('Area'), 'orders');
    await waitFor(() => expect(activityCalls().at(-1)?.path).toMatch(/area=orders/));
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1);
    expect(rows.every((r) => within(r).queryByText('Orders'))).toBe(true);

    await user.type(screen.getByLabelText('From (UTC)'), '2026-05-10');
    await user.type(screen.getByLabelText('To (UTC)'), '2026-05-01');
    expect(screen.getByText(/start date must be on or before/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^export/i })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(activityCalls().at(-1)?.path).toBe('/api/activity?limit=100'));
  });
});
