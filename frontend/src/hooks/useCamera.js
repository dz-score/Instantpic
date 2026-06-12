import { useRef, useCallback } from 'react';
import { logger } from '../utils/logger';

/**
 * Manages persistent WebRTC camera stream.
 * The video element stays mounted in the DOM to prevent stream re-initialization lag.
 */
export default function useCamera() {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const canvasRef = useRef(null);

  const initCamera = useCallback(async () => {
    if (streamRef.current) return; // already active
    try {
      const constraints = {
        video: {
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          facingMode: 'user',
        },
        audio: false,
      };
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      // Monitor for camera disconnection
      stream.getVideoTracks().forEach((track) => {
        track.onended = () => {
          logger.warn('camera', 'camera_disconnected', 'Camera track ended — attempting re-init');
          streamRef.current = null;
          initCamera();
        };
      });
      logger.info('camera', 'camera_init_ok', 'Camera initialized', { width: 1920, height: 1080 });
    } catch (err) {
      logger.error('camera', 'camera_init_fail', `Camera access failed: ${err.message}`, { error: err.message });
    }
  }, []);

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  /** Attach stream to <video> if not yet connected */
  const ensureVideoSrc = useCallback(() => {
    if (videoRef.current && streamRef.current && !videoRef.current.srcObject) {
      videoRef.current.srcObject = streamRef.current;
    }
  }, []);

  /** Grab a single HD frame from the video feed as a base64 JPEG data URI */
  const captureFrame = useCallback(() => {
    if (!videoRef.current) return null;
    const w = videoRef.current.videoWidth || 1920;
    const h = videoRef.current.videoHeight || 1080;
    if (!canvasRef.current) {
      canvasRef.current = document.createElement('canvas');
    }
    const canvas = canvasRef.current;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    // Mirror for selfie mode
    ctx.translate(w, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(videoRef.current, 0, 0, w, h);
    return canvas.toDataURL('image/jpeg', 0.95);
  }, []);

  return { videoRef, initCamera, stopCamera, ensureVideoSrc, captureFrame };
}
