import { useCallback, useEffect, useId, useMemo, useState } from 'react';
import { listActivity } from '../api/endpoints';
import { ACTIONS, AREAS } from '../api/types';
import type { Action, ActivityEvent, Area, ExportFilters } from '../api/types';
import { ExportMenu } from '../components/ExportMenu';
import { actionLabel, areaLabel, formatCount, formatDateTime } from '../lib/format';
import styles from './ActivityPage.module.css';

const PAGE_SIZE = 100;

interface Filters {
  area: Area | '';
  action: Action | '';
  /** yyyy-mm-dd from <input type="date">, interpreted as UTC days */
  from: string;
  to: string;
}

const EMPTY: Filters = { area: '', action: '', from: '', to: '' };

const toIsoStart = (day: string) => (day ? `${day}T00:00:00Z` : undefined);
const toIsoEnd = (day: string) => (day ? `${day}T23:59:59Z` : undefined);

export function ActivityPage() {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [rows, setRows] = useState<ActivityEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const ids = { area: useId(), action: useId(), from: useId(), to: useId(), err: useId() };

  const rangeInvalid = Boolean(filters.from && filters.to && filters.from > filters.to);

  const query = useMemo(
    () => ({
      area: filters.area || undefined,
      action: filters.action || undefined,
      from: toIsoStart(filters.from),
      to: toIsoEnd(filters.to),
    }),
    [filters],
  );

  const exportFilters: ExportFilters = useMemo(() => {
    const f: ExportFilters = {};
    if (query.action) f.action = query.action;
    if (query.from) f.from = query.from;
    if (query.to) f.to = query.to;
    return f;
  }, [query]);

  // First page whenever the filters change.
  useEffect(() => {
    if (rangeInvalid) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    listActivity({ ...query, limit: PAGE_SIZE }, controller.signal)
      .then((page) => {
        setRows(page.items);
        setCursor(page.next_cursor);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setRows([]);
        setCursor(null);
        setError(err instanceof Error ? err.message : 'Could not load activity.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, rangeInvalid]);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setLoading(true);
    try {
      const page = await listActivity({ ...query, limit: PAGE_SIZE, cursor });
      setRows((all) => [...all, ...page.items]);
      setCursor(page.next_cursor);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load more activity.');
    } finally {
      setLoading(false);
    }
  }, [cursor, query]);

  const update = <K extends keyof Filters>(key: K, value: Filters[K]) =>
    setFilters((f) => ({ ...f, [key]: value }));

  const hasFilters = filters.area || filters.action || filters.from || filters.to;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Activity</h1>
          <p>Every action recorded in the product, newest first. Records cannot be edited.</p>
        </div>
        <ExportMenu area={filters.area || 'all'} filters={exportFilters} disabled={rangeInvalid} />
      </div>

      <form
        className={`card ${styles.filters}`}
        aria-label="Filter activity"
        onSubmit={(e) => e.preventDefault()}
      >
        <div className="field">
          <label htmlFor={ids.area}>Area</label>
          <select
            id={ids.area}
            value={filters.area}
            onChange={(e) => update('area', e.target.value as Filters['area'])}
          >
            <option value="">All areas</option>
            {AREAS.map((a) => (
              <option key={a} value={a}>
                {areaLabel(a)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={ids.action}>Action</label>
          <select
            id={ids.action}
            value={filters.action}
            onChange={(e) => update('action', e.target.value as Filters['action'])}
          >
            <option value="">All actions</option>
            {ACTIONS.map((a) => (
              <option key={a} value={a}>
                {actionLabel(a)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={ids.from}>From (UTC)</label>
          <input
            id={ids.from}
            type="date"
            value={filters.from}
            max={filters.to || undefined}
            onChange={(e) => update('from', e.target.value)}
            aria-invalid={rangeInvalid || undefined}
            aria-describedby={rangeInvalid ? ids.err : undefined}
          />
        </div>
        <div className="field">
          <label htmlFor={ids.to}>To (UTC)</label>
          <input
            id={ids.to}
            type="date"
            value={filters.to}
            min={filters.from || undefined}
            onChange={(e) => update('to', e.target.value)}
            aria-invalid={rangeInvalid || undefined}
            aria-describedby={rangeInvalid ? ids.err : undefined}
          />
        </div>
        <div className={styles.reset}>
          <button
            type="button"
            className="btn"
            onClick={() => setFilters(EMPTY)}
            disabled={!hasFilters}
          >
            Clear filters
          </button>
        </div>
        {rangeInvalid && (
          <p id={ids.err} className={`field-error ${styles.fullRow}`} role="alert">
            The start date must be on or before the end date.
          </p>
        )}
      </form>

      <p className={`muted ${styles.summary}`} role="status" aria-live="polite">
        {loading
          ? 'Loading activity…'
          : error
            ? ''
            : `Showing ${formatCount(rows.length)} ${rows.length === 1 ? 'event' : 'events'}${cursor ? ' (more available)' : ''}.`}
      </p>
      {error && (
        <p className="field-error" role="alert">
          Could not load activity: {error}
        </p>
      )}

      <div className={`card ${styles.tableWrap}`} tabIndex={0} aria-labelledby="activity-caption">
        <table className={styles.table} aria-busy={loading}>
          <caption id="activity-caption" className="visually-hidden">
            Activity events, newest first
          </caption>
          <thead>
            <tr>
              <th scope="col">Time</th>
              <th scope="col">Area</th>
              <th scope="col">Action</th>
              <th scope="col">Entity</th>
              <th scope="col">Entity ID</th>
              <th scope="col">Actor</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.event_id}>
                <td className={styles.nowrap}>
                  <time dateTime={row.occurred_at}>{formatDateTime(row.occurred_at)}</time>
                </td>
                <td>{areaLabel(row.area)}</td>
                <td>
                  <span className={styles.action}>{actionLabel(row.action)}</span>
                </td>
                <td>{row.entity_type}</td>
                <td className={styles.mono}>{row.entity_id}</td>
                <td className={styles.mono}>{row.actor_id}</td>
              </tr>
            ))}
            {!loading && !error && rows.length === 0 && (
              <tr>
                <td colSpan={6} className={styles.empty}>
                  No activity matches these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {cursor && (
        <div className={styles.more}>
          <button type="button" className="btn" onClick={() => void loadMore()} disabled={loading}>
            {loading ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
    </div>
  );
}
