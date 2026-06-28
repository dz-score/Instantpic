import { useState, useCallback, useEffect } from 'react';
import { logger } from '../utils/logger';

export default function useCamera(cameraStatus) {
  const [mode, setMode] = useState('gphoto2'); // Always use gphoto2


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

  const resumePreview = useCallback(async () => {
    try {
      logger.info('camera', 'resume_start', 'Waking up backend camera worker');
      await fetch('/api/camera/resume', { method: 'POST' });
    } catch (err) {
      logger.error('camera', 'resume_fail', `Failed to resume camera: ${err.message}`);
    }
  }, []);

  const standbyPreview = useCallback(async () => {
    try {
      logger.info('camera', 'standby_start', 'Pausing backend camera worker (pre-capture)');
      await fetch('/api/camera/standby', { method: 'POST' });
    } catch (err) {
      logger.error('camera', 'standby_fail', `Failed to standby camera: ${err.message}`);
    }
  }, []);

  return { previewUrl, captureFrame, resumePreview, standbyPreview, mode, cameraStatus };
}
