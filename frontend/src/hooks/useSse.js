import { useState, useEffect, useRef } from 'react';

const SSE_URL = '/api/sse';

export default function useSse() {
  const [cameraStatus, setCameraStatus] = useState({
    connected: false,
    is_capturing: false,
    error: null,
  });
  const [printerStatus, setPrinterStatus] = useState({
    printer_name: null,
    total_jobs_sent: 0,
    failed_jobs: 0,
    is_online: false,
  });
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

      eventSource.addEventListener('printer_status', (e) => {
        try {
          const data = JSON.parse(e.data);
          setPrinterStatus(data);
        } catch (err) {
          console.error('Failed to parse printer_status SSE:', err);
        }
      });

      eventSource.onerror = (e) => {
        console.error('SSE connection error, attempting to reconnect...', e);
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

  return { cameraStatus, printerStatus, isOnline };
}
