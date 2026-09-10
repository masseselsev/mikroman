import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { useWebSocketTelemetry } from './useWebSocketTelemetry';

/**
 * The monitoring page is the router's loudest client: one WebSocket frame per
 * second, each frame a handful of RouterOS reads. These tests pin the two
 * properties that keep that from turning into runaway load — the socket stops
 * when nobody is looking, and a page never holds two sockets at once.
 */

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.closed = false;
    FakeWebSocket.instances.push(this);
  }

  close() {
    this.closed = true;
    this.readyState = FakeWebSocket.CLOSED;
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  drop() {
    // A link failure, which is what the reconnect timer exists for.
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
}

function setVisibility(value) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => value,
  });
}

describe('useWebSocketTelemetry', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    setVisibility('visible');
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const visibleSockets = () => FakeWebSocket.instances.filter((s) => !s.closed);

  it('opens exactly one socket per mounted page', () => {
    renderHook(() => useWebSocketTelemetry(null));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain('/ws/telemetry');
  });

  it('closes the socket when the tab is hidden and stays closed', () => {
    const { unmount } = renderHook(() => useWebSocketTelemetry(null));
    const first = FakeWebSocket.instances[0];
    // `open()` runs the hook's onopen, which sets state; keep it inside act().
    act(() => { first.open(); });

    act(() => {
      setVisibility('hidden');
      document.dispatchEvent(new Event('visibilitychange'));
    });

    expect(first.closed).toBe(true);
    expect(visibleSockets()).toHaveLength(0);
    unmount();
  });

  it('does not reconnect while hidden', () => {
    vi.useFakeTimers();
    renderHook(() => useWebSocketTelemetry(null));
    const first = FakeWebSocket.instances[0];
    // `open()` runs the hook's onopen, which sets state; keep it inside act().
    act(() => { first.open(); });

    act(() => {
      setVisibility('hidden');
      document.dispatchEvent(new Event('visibilitychange'));
    });
    // A dropped connection arriving while hidden must not schedule a retry:
    // that is how a background tab ends up polling a router overnight.
    act(() => { first.drop(); });
    act(() => { vi.advanceTimersByTime(10_000); });

    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.useRealTimers();
  });

  it('reconnects when the tab becomes visible again', () => {
    renderHook(() => useWebSocketTelemetry(null));
    act(() => {
      setVisibility('hidden');
      document.dispatchEvent(new Event('visibilitychange'));
    });
    act(() => {
      setVisibility('visible');
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(visibleSockets()).toHaveLength(1);
  });

  it('never stacks a second live socket for the same page', () => {
    renderHook(() => useWebSocketTelemetry(null));
    act(() => { FakeWebSocket.instances[0].open(); });

    // Two visibility flips in a row must not produce two connections.
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(visibleSockets()).toHaveLength(1);
  });

  it('retries after an unexpected drop while visible', () => {
    vi.useFakeTimers();
    renderHook(() => useWebSocketTelemetry(null));
    const first = FakeWebSocket.instances[0];
    // `open()` runs the hook's onopen, which sets state; keep it inside act().
    act(() => { first.open(); });

    act(() => { first.drop(); });
    act(() => { vi.advanceTimersByTime(3000); });

    expect(FakeWebSocket.instances).toHaveLength(2);
    vi.useRealTimers();
  });

  it('tears the socket down on unmount without scheduling a reconnect', () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useWebSocketTelemetry(null));
    const first = FakeWebSocket.instances[0];
    // `open()` runs the hook's onopen, which sets state; keep it inside act().
    act(() => { first.open(); });

    unmount();
    act(() => { vi.advanceTimersByTime(10_000); });

    expect(first.closed).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.useRealTimers();
  });
});
