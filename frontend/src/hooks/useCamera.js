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

  // No standbyPreview: the frontend used to pause live view just before firing,
  // but the backend is capture-authoritative now (camera_service gates the
  // preview worker at enqueue). Calling it from here only froze the preview
  // early and widened the shot-to-shot gap. The /api/camera/standby endpoint
  // still exists; the booth's own watchdog is its only caller.

  return { previewUrl, resumePreview, mode, cameraStatus };
}
