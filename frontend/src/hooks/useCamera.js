import { useState, useCallback, useEffect } from 'react';
import { logger } from '../utils/logger';

export default function useCamera(cameraStatus) {
  const [mode, setMode] = useState('gphoto2'); // Always use gphoto2


  const previewUrl = '/api/camera/preview';

  // NOTE: capture is no longer triggered here. The countdown fires the
  // shutter via the FSM (FIRE_SHOT event), and completion returns to the
  // FSM through backend-owned callbacks — the browser is not in that loop.

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

  return { previewUrl, resumePreview, standbyPreview, mode, cameraStatus };
}
