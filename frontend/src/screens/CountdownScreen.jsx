import React, { useState, useEffect, useRef, useCallback } from 'react';
import ProgressDots from '../components/ProgressDots';
import { playShutterSound } from '../utils/sounds';
import { t } from '../utils/i18n';
import './CountdownScreen.css';

const BETWEEN_SHOT_DELAY = 3000; // ms between collage shots

/**
 * Full-screen camera feed with countdown overlay.
 * Handles entire capture sequence internally:
 * - Single mode: 1 countdown → 1 capture → callback
 * - Collage mode: 3 rounds of (countdown → capture → interstitial)
 *
 * The <video> element is passed in from App so it stays mounted.
 */
export default function CountdownScreen({
  previewUrl,
  layoutMode,
  captureFrame,
  resumePreview,
  onComplete,
  config,
  language,
}) {
  const COUNTDOWN_FROM = config?.countdown_duration || 3;
  const flashEnabled = config?.flash_enabled !== false;
  const [phase, setPhase] = useState('COUNTDOWN'); // COUNTDOWN | BETWEEN
  const [count, setCount] = useState(COUNTDOWN_FROM);
  const [shotIndex, setShotIndex] = useState(0);
  const [flashActive, setFlashActive] = useState(false);
  const [lastCapture, setLastCapture] = useState(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [sessionStarted, setSessionStarted] = useState(false);
  const pendingTimeouts = useRef([]);
  const imagesRef = useRef([]);
  const totalShots = layoutMode === 'collage' ? 3 : 1;
  const timerRef = useRef(null);
  const countdownVideoRef = useRef(null);
  // Stable MJPEG src — set once on mount, never changes.
  // This ensures exactly ONE backend preview connection for the entire session.
  const previewSrc = useRef(`${previewUrl}?t=${Date.now()}`);

  const safeTimeout = useCallback((fn, ms) => {
    const id = setTimeout(fn, ms);
    pendingTimeouts.current.push(id);
    return id;
  }, []);

  // Run a single countdown round
  const runCountdown = useCallback((onDone) => {
    setPhase('COUNTDOWN');
    let c = COUNTDOWN_FROM;
    setCount(c);

    // Calculate start time based on countdown duration.
    // Video is 10s. Time offsets provided: 9 is 1.1s, 8 is 2.1s, 3 is 7.1s
    let startTime = 0;
    if (COUNTDOWN_FROM <= 10) {
      startTime = COUNTDOWN_FROM === 10 ? 0 : (10 - COUNTDOWN_FROM) + 0.1;
    }

    // Hardware accelerated restart of video
    if (countdownVideoRef.current) {
      countdownVideoRef.current.currentTime = startTime;
      countdownVideoRef.current.play().catch(err => console.log('Video playback error:', err));
    }

    timerRef.current = setInterval(() => {
      c -= 1;
      if (c > 0) {
        setCount(c);
      } else if (c === 0) {
        // Countdown finished (guest just saw "1").
        // Hide the countdown overlay and give them ~1 second
        // of pure live view to hold their pose before capture.
        setPhase('POSING');
      } else {
        // c < 0 — the pose gap is over, fire the shutter
        clearInterval(timerRef.current);
        onDone();
      }
    }, 1000);
  }, [COUNTDOWN_FROM]);

  // Fire shutter: capture, then flash + sound on success (with auto-retry)
  const fireShutter = useCallback(async () => {
    setIsCapturing(true);

    let frame = await captureFrame();
    
    // Auto-retry once if capture failed (handles transient camera errors)
    if (!frame) {
      console.warn('[CountdownScreen] Capture failed, retrying in 1.5s...');
      await new Promise(r => setTimeout(r, 1500));
      frame = await captureFrame();
    }

    // Flash + sound AFTER capture succeeds — synced with the actual photo
    if (frame) {
      if (flashEnabled) {
        setFlashActive(true);
        safeTimeout(() => setFlashActive(false), 250);
      }
      playShutterSound();
    }
    
    setIsCapturing(false);
    
    if (frame) {
      imagesRef.current = [...imagesRef.current, frame];
      setLastCapture(`/photos/${frame}`);
    }

    return frame;
  }, [captureFrame, flashEnabled, safeTimeout]);

  const startRound = useCallback(async (idx) => {
    setShotIndex(idx);
    
    // Wake up the camera worker from standby
    if (resumePreview) {
      await resumePreview();
    }
    
    runCountdown(async () => {
      await fireShutter();
      if (idx + 1 >= totalShots) {
        // All shots taken — transition immediately (no preview resumption)
        onComplete(imagesRef.current);
      } else {
        // Show between-shots interstitial
        setPhase('BETWEEN');
        safeTimeout(() => {
          startRound(idx + 1);
        }, BETWEEN_SHOT_DELAY);
      }
    });
  }, [runCountdown, fireShutter, totalShots, safeTimeout, onComplete, resumePreview]);

  // 1. Mount: Wake up camera and subscribe to SSE
  useEffect(() => {
    imagesRef.current = [];
    setShotIndex(0);

    if (resumePreview) {
      resumePreview();
    }

    // Subscribe to SSE for diagnostic metrics
    const evtSource = new EventSource('/api/events');
    evtSource.addEventListener('camera_metrics', (e) => {
      try {
        const data = JSON.parse(e.data);
        const el = document.getElementById('diag-metrics');
        if (el) {
          el.innerHTML = `FPS: ${data.fps} | Latency: ${data.latency_ms}ms<br/>` +
                         `Worker: ${data.worker_running ? 'ON' : 'OFF'} | Allowed: ${data.allowed ? 'YES' : 'NO'}<br/>` +
                         `Time since last: ${data.time_since_last_frame_ms}ms`;
        }
      } catch (err) {}
    });

    return () => {
      clearInterval(timerRef.current);
      pendingTimeouts.current.forEach(clearTimeout);
      pendingTimeouts.current = [];
      evtSource.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2. Start session only when camera stream actually loads
  useEffect(() => {
    if (cameraReady && !sessionStarted) {
      setSessionStarted(true);
      startRound(0);
    }
  }, [cameraReady, sessionStarted, startRound]);


  return (
    <div className="countdown-screen">
      {/* Camera feed — full bleed */}
      <div className="countdown-viewport">
        {/* MJPEG stream — always mounted to keep a single backend connection.
            Hidden via CSS during capture so the browser doesn't close/reopen the stream. */}
        <img
          src={previewSrc.current}
          className="countdown-video"
          alt="Camera Live View"
          onLoad={() => setCameraReady(true)}
          style={{ opacity: cameraReady ? 1 : 0, transition: 'opacity 0.3s' }}
        />

        {/* Loading Spinner for cold start */}
        {!cameraReady && (
          <div className="countdown-loading">
            <div className="spinner"></div>
            <p>{t('camera.wakingUp', language) || "Waking up camera..."}</p>
          </div>
        )}

        {/* Warm overlay tint */}
        <div className="countdown-overlay" />

        {/* Flash effect (warm champagne) */}
        <div className={`countdown-flash ${flashActive ? 'countdown-flash--active' : ''}`} />

        {/* Countdown video - always mounted for performance, toggled via opacity */}
        <div 
          className="countdown-center" 
          style={{ opacity: (phase === 'COUNTDOWN' && !isCapturing) ? 1 : 0, transition: 'opacity 0.2s' }}
        >
          <video 
            ref={countdownVideoRef}
            src="/countdown.mp4" 
            muted 
            playsInline 
            preload="auto"
            className="countdown-ring-video" 
          />
        </div>

        {phase === 'BETWEEN' && !isCapturing && (
          <div className="countdown-between">
            <div className="countdown-between__preview">
              {lastCapture && (
                <img src={lastCapture} alt="Last shot" className="countdown-between__img" />
              )}
            </div>
            <p className="countdown-between__text">
              {totalShots - (shotIndex + 1) === 1
                ? t('countdown.oneMore', language)
                : t('countdown.moreToGo', language).replace('{n}', totalShots - (shotIndex + 1))
              }
            </p>
          </div>
        )}
        


        {/* Progress dots (collage only) */}
        {layoutMode === 'collage' && (
          <div className="countdown-progress">
            <ProgressDots current={shotIndex} total={totalShots} />
          </div>
        )}

        {/* --- Diagnostic Overlay --- */}
        <div style={{
          position: 'absolute',
          top: '10px',
          left: '10px',
          background: 'rgba(0,0,0,0.7)',
          color: '#0f0',
          padding: '10px',
          fontFamily: 'monospace',
          fontSize: '12px',
          zIndex: 9999,
          pointerEvents: 'none',
          borderRadius: '4px'
        }}>
          <b>Diagnostics</b><br/>
          Phase: {phase}<br/>
          Capturing: {isCapturing ? 'Yes' : 'No'}<br/>
          Metrics: <span id="diag-metrics">Waiting...</span>
        </div>
      </div>
    </div>
  );
}
