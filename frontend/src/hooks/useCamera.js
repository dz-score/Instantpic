import { useState, useCallback, useEffect } from 'react';
import { logger } from '../utils/logger';

export default function useCamera() {
  const [mode, setMode] = useState('gphoto2'); // Always use gphoto2
  const [cameraStatus, setCameraStatus] = useState({ connected: false, error: null });

  // Poll camera status from backend
  useEffect(() => {
    let active = true;
    const checkStatus = async () => {
      try {
        const res = await fetch('/api/camera/status');
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();
        if (active) {
          setCameraStatus({ connected: data.connected, error: data.error || null });
        }
      } catch (err) {
        if (active) {
          setCameraStatus({ connected: false, error: 'Cannot reach backend camera service' });
        }
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const previewUrl = '/api/camera/preview';

  /** Grab a single high-quality frame from the camera */
  const captureFrame = useCallback(async () => {
    try {
      logger.info('camera', 'capture_start', 'Triggering backend capture');
      const res = await fetch('/api/camera/capture', { method: 'POST' });
      if (!res.ok) {
        throw new Error(`Capture failed with status: ${res.status}`);
      }
      const data = await res.json();
      logger.info('camera', 'capture_ok', 'Backend capture successful', { filename: data.filename });
      // Return the filename instead of a base64 string
      return data.filename;
    } catch (err) {
      logger.error('camera', 'capture_fail', `Backend capture failed: ${err.message}`);
      return null;
    }
  }, []);

  return { previewUrl, captureFrame, mode, cameraStatus };
}
