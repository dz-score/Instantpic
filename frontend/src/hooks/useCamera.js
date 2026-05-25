import { useRef, useCallback } from 'react';

/**
 * Manages persistent WebRTC camera stream.
 * The video element stays mounted in the DOM to prevent stream re-initialization lag.
 */
export default function useCamera() {
  const videoRef = useRef(null);
  const streamRef = useRef(null);

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
    } catch (err) {
      console.error('Camera access failed:', err);
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
    const canvas = document.createElement('canvas');
    canvas.width = 1920;
    canvas.height = 1080;
    const ctx = canvas.getContext('2d');
    // Mirror for selfie mode
    ctx.translate(canvas.width, 0);
    ctx.scale(-1, 1);
    ctx.drawImage(videoRef.current, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.95);
  }, []);

  return { videoRef, initCamera, stopCamera, ensureVideoSrc, captureFrame };
}
