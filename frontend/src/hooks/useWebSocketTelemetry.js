import { useState, useEffect, useRef } from 'react';

export function useWebSocketTelemetry(routerId = null) {
  const [telemetry, setTelemetry] = useState(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    // Drop the previous router's last frame the moment the selection changes,
    // so its CPU / traffic / user list do not linger on screen until the new
    // socket delivers its first tick.
    setTelemetry(null);
    setIsConnected(false);

    let reconnectTimeout = null;
    let isCancelled = false;

    function closeSocket() {
      // Detach the handlers first: `onclose` schedules a reconnect, and a
      // deliberate close (hidden tab, unmount, router switch) must not start one.
      const ws = wsRef.current;
      wsRef.current = null;
      if (!ws) return;
      ws.onclose = null;
      ws.onmessage = null;
      ws.onerror = null;
      try { ws.close(); } catch { /* already closing */ }
      setIsConnected(false);
    }

    function connect() {
      if (isCancelled) return;
      // Never stack sockets. Each live connection drives its own per-second
      // poll of the router, so a second one for the same page doubles the load
      // the operator is watching for.
      const open = wsRef.current
        && (wsRef.current.readyState === WebSocket.CONNECTING || wsRef.current.readyState === WebSocket.OPEN);
      if (open) return;

      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      const query = routerId ? `?router_id=${routerId}` : '';
      const wsUrl = `${protocol}//${host}/ws/telemetry${query}`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'telemetry_tick') {
            setTelemetry(data);
          }
        } catch (err) {
          console.error('Failed to parse telemetry tick', err);
        }
      };

      ws.onclose = (event) => {
        setIsConnected(false);
        // Do not hammer server on auth policy violation (code 1008)
        if (event && event.code === 1008) {
          return;
        }
        // Automatic reconnection attempt after 2.5s, unless the page went away.
        if (!isCancelled && document.visibilityState !== 'hidden') {
          reconnectTimeout = setTimeout(connect, 2500);
        }
      };

      ws.onerror = (err) => {
        console.warn('WebSocket connection error:', err);
        ws.close();
      };
    }

    // A monitoring page left open in a background tab was polling the router
    // once a second indefinitely, at full cost, for nobody. Stop while hidden,
    // resume the moment it is visible again; the first frame lands on return.
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        if (reconnectTimeout) { clearTimeout(reconnectTimeout); reconnectTimeout = null; }
        closeSocket();
      } else {
        connect();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    connect();

    return () => {
      isCancelled = true;
      document.removeEventListener('visibilitychange', onVisibility);
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      closeSocket();
    };
  }, [routerId]);

  return { telemetry, isConnected };
}
