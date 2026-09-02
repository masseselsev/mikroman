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

    function connect() {
      if (isCancelled) return;
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

      ws.onclose = () => {
        setIsConnected(false);
        // Automatic reconnection attempt after 2.5s if not cancelled
        if (!isCancelled) {
          reconnectTimeout = setTimeout(connect, 2500);
        }
      };

      ws.onerror = (err) => {
        console.warn('WebSocket connection error:', err);
        ws.close();
      };
    }

    connect();

    return () => {
      isCancelled = true;
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (wsRef.current) wsRef.current.close();
    };
  }, [routerId]);

  return { telemetry, isConnected };
}
