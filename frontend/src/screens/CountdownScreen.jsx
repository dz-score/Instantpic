import React, { useState, useEffect, useRef, useCallback } from 'react';
import ProgressDots from '../components/ProgressDots';
import { playShutterSound } from '../utils/sounds';
import { t } from '../utils/i18n';
import { Home } from 'lucide-react';
import './CountdownScreen.css';

const BETWEEN_SHOT_DELAY = 3000; // ms between collage shots

/**
 * Full-screen camera feed with countdown overlay.
 *
 * Presentation + input + dispatch only. This screen renders the live view and
 * countdown, plays capture effects, invokes the backend capture action, and
 * reports each completed shot back to the FSM via `onShotCaptured`.
 *
 * It does NOT decide how many shots a layout needs or when the sequence is
 * finished — that workflow authority lives in the backend FSM. `totalShots`
 * arrives as backend state, and the backend advances to REVEAL once it has
 * received all the shots (which unmounts this screen).
 *
 * Camera events are consumed from the app's single SSE stream (`cameraJob`,
 * `cameraMetrics`) passed down as props, rather than opening a second stream.
 */
export default function CountdownScreen({
  previewUrl,
  totalShots = 1,
  capturedCount = 0,
  captureFrame,
  resumePreview,
  standbyPreview,
  cameraJob,
  cameraMetrics,
  onShotCaptured,
  onCancel,
  config,
  language,
}) {
  const COUNTDOWN_FROM = config?.countdown_duration || 3;
  const flashEnabled = config?.flash_enabled !== false;
  const [phase, setPhase] = useState('COUNTDOWN'); // COUNTDOWN | POSING | BETWEEN
  const [count, setCount] = useState(COUNTDOWN_FROM);
  const [shotIndex, setShotIndex] = useState(0);
  const [flashActive, setFlashActive] = useState(false);
  const [lastCapture, setLastCapture] = useState(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState(false);
  const [sessionStarted, setSessionStarted] = useState(false);

  useEffect(() => {
    if (cameraReady) return;
    const t = setTimeout(() => setCameraError(true), 10000);
    return () => clearTimeout(t);
  }, [cameraReady]);
  const pendingTimeouts = useRef([]);
  const totalShotsRef = useRef(totalShots);
  totalShotsRef.current = totalShots;
  const timerRef = useRef(null);
  const countdownVideoRef = useRef(null);
  const captureResolvers = useRef({});
  // Stable MJPEG src — set once on mount, never changes.
  // This ensures exactly ONE backend preview connection for the entire session.
  const previewSrc = useRef(`${previewUrl}?t=${Date.now()}`);

  const safeTimeout = useCallback((fn, ms) => {
    const id = setTimeout(fn, ms);
    pendingTimeouts.current.push(id);
    return id;
  }, []);

  // Route capture-lifecycle events from the app's central SSE stream to the
  // resolver registered for the in-flight job. Only presentation effects and
  // the "report this shot" dispatch happen here.
  useEffect(() => {
    if (!cameraJob) return;
    const resolvers = captureResolvers.current[cameraJob.job_id];
    if (!resolvers) return;

    if (cameraJob.status === 'fired' && resolvers.onFired) {
      resolvers.onFired();
      resolvers.onFired = null; // Prevent duplicate fires if SSE re-delivers
    } else if (cameraJob.status === 'completed' && resolvers.onCompleted) {
      resolvers.onCompleted(cameraJob.filename);
      delete captureResolvers.current[cameraJob.job_id];
    } else if (cameraJob.status === 'failed' && resolvers.onFailed) {
      resolvers.onFailed(cameraJob.error);
      delete captureResolvers.current[cameraJob.job_id];
    }
  }, [cameraJob]);

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
      c -= 0.25;
      if (c > 0) {
        if (c % 1 === 0) setCount(c);
      } else if (c === 0) {
        // At 0, we show "Pose!" and let the user hold their pose.
        setPhase('POSING');
        // We stop the live view polling now so the camera's USB bus has a brief moment
        // to settle before the heavy high-res capture command.
        // With active event flushing in place, a short 250ms gap is sufficient.
        if (standbyPreview) {
          standbyPreview();
        }
      } else {
        // c < 0 — the pose gap is over, fire the shutter
        clearInterval(timerRef.current);
        onDone();
      }
    }, 250);
  }, [COUNTDOWN_FROM, standbyPreview]);

  const triggerCapture = useCallback(async (attempt = 1) => {
    setIsCapturing(true);
    const jobId = await captureFrame();

    if (!jobId) {
      setIsCapturing(false);
      return null;
    }

    return new Promise((resolve) => {
      captureResolvers.current[jobId] = {
        onFired: () => {
          // Flash + sound EXACTLY when the backend confirms the shutter opened!
          if (flashEnabled) {
            setFlashActive(true);
            safeTimeout(() => setFlashActive(false), 250);
          }
          playShutterSound();
        },
        onCompleted: (filename) => {
          setIsCapturing(false);
          setLastCapture(`/photos/${filename}`);
          // Report the shot to the FSM — the backend owns accumulation and
          // decides when the sequence is complete.
          if (onShotCaptured) onShotCaptured(filename);
          resolve(filename);
        },
        onFailed: async (error) => {
          console.warn(`[CountdownScreen] Capture failed (attempt ${attempt}):`, error);
          if (attempt === 1) {
            await new Promise(r => setTimeout(r, 1500));
            const result = await triggerCapture(2);
            resolve(result);
          } else {
            setIsCapturing(false);
            resolve(null);
          }
        }
      };
    });
  }, [captureFrame, flashEnabled, safeTimeout, onShotCaptured]);

  // Fire shutter orchestrator
  const fireShutter = useCallback(async () => {
    return await triggerCapture(1);
  }, [triggerCapture]);

  const startRound = useCallback(async (idx) => {
    setShotIndex(idx);

    // Wake up the camera worker from standby
    if (resumePreview) {
      await resumePreview();
    }

    runCountdown(async () => {
      await fireShutter();
      // The backend decides completion. If more shots are still expected for
      // this layout, show the interstitial and run the next round; otherwise
      // the FSM will transition to REVEAL and unmount this screen.
      if (idx + 1 < totalShotsRef.current) {
        setPhase('BETWEEN');
        safeTimeout(() => {
          startRound(idx + 1);
        }, BETWEEN_SHOT_DELAY);
      }
    });
  }, [runCountdown, fireShutter, safeTimeout, resumePreview]);

  // 1. Mount: wake up the camera. Camera events arrive via props (central SSE).
  useEffect(() => {
    setShotIndex(0);

    if (resumePreview) {
      resumePreview();
    }

    return () => {
      clearInterval(timerRef.current);
      pendingTimeouts.current.forEach(clearTimeout);
      pendingTimeouts.current = [];
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
          onError={() => setCameraError(true)}
          style={{ opacity: cameraReady ? 1 : 0, transition: 'opacity 0.3s' }}
        />

        {/* Error State for camera timeout/failure */}
        {cameraError && !cameraReady && (
          <div className="countdown-loading">
            <div className="countdown-loading-glow" aria-hidden="true" />
            <div className="countdown-loading-content">
              <p className="countdown-loading__kicker" style={{ color: 'var(--error)' }}>
                {t('camera.error', language) || "Camera Connection Failed"}
              </p>
              <p className="countdown-loading__sub" style={{ marginTop: '8px' }}>
                {t('camera.errorSub', language) || "Please check the camera connection"}
              </p>
              <button
                className="countdown-btn-home"
                onClick={onCancel}
                style={{ marginTop: '2rem' }}
              >
                <span className="btn-icon"><Home strokeWidth={1.5} size={20} /></span>
                <span>{t('reveal.home', language)}</span>
              </button>
            </div>
          </div>
        )}

        {/* Loading Spinner for cold start */}
        {!cameraReady && !cameraError && (
          <div className="countdown-loading">
            <div className="countdown-loading-glow" aria-hidden="true" />
            <div className="countdown-loading-content">
              <div className="countdown-spinner"></div>
              <p className="countdown-loading__kicker">{t('camera.loading', language) || "Camera is loading"}</p>
              <p className="countdown-loading__sub">{t('reveal.justAMoment', language)}</p>
            </div>
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



        {/* Progress dots (multi-shot layouts only) */}
        {totalShots > 1 && (
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
          Shots: {capturedCount}/{totalShots}<br/>
          Metrics: <span>
            {cameraMetrics
              ? `FPS: ${cameraMetrics.fps} | Latency: ${cameraMetrics.latency_ms}ms | ` +
                `Worker: ${cameraMetrics.worker_running ? 'ON' : 'OFF'} | ` +
                `Allowed: ${cameraMetrics.allowed ? 'YES' : 'NO'}`
              : 'Waiting...'}
          </span>
        </div>
      </div>
    </div>
  );
}
