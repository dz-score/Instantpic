import { useState, useEffect, useRef } from 'react';
import { logger } from '../utils/logger';

const SSE_URL = '/api/sse';

export default function useSse() {
  const [cameraStatus, setCameraStatus] = useState({
    connected: false,
    is_capturing: false,
    error: null,
  });
  const [backendState, setBackendState] = useState(null);
  const [config, setConfig] = useState(null);
  const [cameraJob, setCameraJob] = useState(null);
  const [cameraMetrics, setCameraMetrics] = useState(null);
  const [isOnline, setIsOnline] = useState(false);
  const eventSourceRef = useRef(null);

  useEffect(() => {
    let reconnectTimer;

    const connect = () => {
      // Use absolute URL or relative depending on dev vs prod
      const url = import.meta.env.DEV ? `http://localhost:8000${SSE_URL}` : SSE_URL;
      const eventSource = new EventSource(url);
      eventSourceRef.current = eventSource;

      eventSource.onopen = () => {
        console.log('SSE connection opened');
        logger.info('sse', 'sse_open', 'SSE connection opened');
        setIsOnline(true);
      };

      eventSource.addEventListener('camera_status', (e) => {
        try {
          const data = JSON.parse(e.data);
          setCameraStatus(data);
        } catch (err) {
          console.error('Failed to parse camera_status SSE:', err);
        }
      });

      // Logged on arrival, before setBackendState, so the log distinguishes
      // "the update never reached the browser" from "it reached the browser
      // but the UI never rendered it" — the two look identical otherwise, and
      // that ambiguity is what made the 2026-07-13 reveal freeze undiagnosable.
      eventSource.addEventListener('state_update', (e) => {
        try {
          const data = JSON.parse(e.data);
          logger.info('sse', 'sse_state_update', `Received state_update: ${data.screen}`, {
            screen: data.screen,
            finalPhoto: data.finalPhoto,
            isProcessing: data.isProcessing,
            printStatus: data.printStatus,
          });
          setBackendState(data);
        } catch (err) {
          console.error('Failed to parse state_update SSE:', err);
          logger.error('sse', 'sse_state_parse_fail', `Failed to parse state_update: ${err.message}`);
        }
      });

      // Config is pushed by the backend on connect and on every change, so the
      // frontend always holds a fresh copy without a separate REST fetch.
      eventSource.addEventListener('config_update', (e) => {
        try {
          setConfig(JSON.parse(e.data));
        } catch (err) {
          console.error('Failed to parse config_update SSE:', err);
        }
      });

      // Camera capture lifecycle (pending/started/fired/downloading/completed/failed).
      // Exposed centrally so screens consume the single app event stream rather
      // than opening their own EventSource connections.
      eventSource.addEventListener('camera_job', (e) => {
        try {
          setCameraJob(JSON.parse(e.data));
        } catch (err) {
          console.error('Failed to parse camera_job SSE:', err);
        }
      });

      eventSource.addEventListener('camera_metrics', (e) => {
        try {
          setCameraMetrics(JSON.parse(e.data));
        } catch (err) {
          console.error('Failed to parse camera_metrics SSE:', err);
        }
      });

      eventSource.onerror = (e) => {
        console.error('SSE connection error, attempting to reconnect...', e);
        // ERROR level flushes the log buffer immediately rather than waiting
        // for the 10s timer — a dropped stream is exactly when we can least
        // afford to lose the buffered lines explaining what led up to it.
        logger.error('sse', 'sse_error', 'SSE connection error, reconnecting in 3s', {
          readyState: eventSource.readyState,
        });
        setIsOnline(false);
        eventSource.close();
        reconnectTimer = setTimeout(connect, 3000); // Try to reconnect in 3s
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  return { cameraStatus, isOnline, backendState, config, cameraJob, cameraMetrics };
}
