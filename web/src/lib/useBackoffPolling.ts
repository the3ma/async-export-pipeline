import { useEffect, useRef } from 'react';
import { usePageVisible } from './usePageVisible';

export interface BackoffPollingOptions {
  /** Poll only while true (e.g. while any job is queued/running). */
  active: boolean;
  /** First delay; also the delay after a reset. */
  minMs?: number;
  /** Delays double after each poll up to this cap. */
  maxMs?: number;
  /** Changing this restarts the schedule at `minMs` (e.g. after queuing a new job). */
  resetKey?: unknown;
}

/**
 * Calls `poll` on a doubling schedule (min → max) while `active` and the tab is visible.
 * Stops as soon as `active` turns false; pauses while the tab is hidden and polls
 * immediately when the tab becomes visible again.
 */
export function useBackoffPolling(
  poll: () => Promise<unknown>,
  { active, minMs = 5_000, maxMs = 30_000, resetKey }: BackoffPollingOptions,
): void {
  const visible = usePageVisible();
  const pollRef = useRef(poll);
  const resumedFromHidden = useRef(false);

  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);

  useEffect(() => {
    if (!visible) {
      resumedFromHidden.current = true;
      return;
    }
    if (!active) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let delay = minMs;

    const tick = async () => {
      try {
        await pollRef.current();
      } catch {
        // Errors are surfaced by the caller's state; keep backing off.
      }
      if (cancelled) return;
      delay = Math.min(delay * 2, maxMs);
      timer = setTimeout(tick, delay);
    };

    const firstDelay = resumedFromHidden.current ? 0 : minMs;
    resumedFromHidden.current = false;
    timer = setTimeout(tick, firstDelay);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [active, visible, minMs, maxMs, resetKey]);
}
