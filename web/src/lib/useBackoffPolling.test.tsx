import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useBackoffPolling } from './useBackoffPolling';

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  document.dispatchEvent(new Event('visibilitychange'));
}

describe('useBackoffPolling', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    setVisibility('visible');
  });

  it('backs off 5 s → 10 s → 20 s → 30 s (cap) while active', async () => {
    const poll = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useBackoffPolling(poll, { active: true }));

    await act(() => vi.advanceTimersByTimeAsync(4_999));
    expect(poll).toHaveBeenCalledTimes(0);
    await act(() => vi.advanceTimersByTimeAsync(1)); // t = 5 s
    expect(poll).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(10_000)); // t = 15 s
    expect(poll).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(20_000)); // t = 35 s
    expect(poll).toHaveBeenCalledTimes(3);
    await act(() => vi.advanceTimersByTimeAsync(29_999));
    expect(poll).toHaveBeenCalledTimes(3);
    await act(() => vi.advanceTimersByTimeAsync(1)); // t = 65 s
    expect(poll).toHaveBeenCalledTimes(4);
    await act(() => vi.advanceTimersByTimeAsync(30_000)); // capped
    expect(poll).toHaveBeenCalledTimes(5);
  });

  it('stops when inactive and restarts at 5 s on resetKey change', async () => {
    const poll = vi.fn().mockResolvedValue(undefined);
    const { rerender } = renderHook(
      ({ active, resetKey }) => useBackoffPolling(poll, { active, resetKey }),
      { initialProps: { active: true, resetKey: 0 } },
    );
    await act(() => vi.advanceTimersByTimeAsync(15_000));
    expect(poll).toHaveBeenCalledTimes(2);

    rerender({ active: false, resetKey: 0 });
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(poll).toHaveBeenCalledTimes(2);

    rerender({ active: true, resetKey: 1 });
    await act(() => vi.advanceTimersByTimeAsync(5_000));
    expect(poll).toHaveBeenCalledTimes(3);
  });

  it('pauses while the tab is hidden and polls immediately when visible again', async () => {
    const poll = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useBackoffPolling(poll, { active: true }));

    act(() => setVisibility('hidden'));
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(poll).not.toHaveBeenCalled();

    act(() => setVisibility('visible'));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it('keeps polling after a failed poll', async () => {
    const poll = vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValue(undefined);
    renderHook(() => useBackoffPolling(poll, { active: true, minMs: 100, maxMs: 100 }));
    await act(() => vi.advanceTimersByTimeAsync(300));
    expect(poll).toHaveBeenCalledTimes(3);
  });
});
