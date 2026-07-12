import React, { useState, useEffect, useRef, useCallback } from 'react';
import ProgressDots from '../components/ProgressDots';
import { playShutterSound } from '../utils/sounds';
import { logger } from '../utils/logger';
import { t } from '../utils/i18n';
import { Home } from 'lucide-react';
import './CountdownScreen.css';

/**
 * Full-screen camera feed with countdown overlay.
 *
 * Presentation + trigger only. This screen renders the live view and
 * countdown, fires the shutter via the FSM (`fireShot` -> FIRE_SHOT), and
 * plays capture effects. Capture *completion* never passes through here:
 * the camera reports straight to the FSM (backend-owned callbacks, same
 * pattern as print/process), and this screen advances rounds purely off
 * `capturedCount` from backend state.
 *
 * It does NOT decide how many shots a layout needs, when the sequence is
 * finished, how long to pace between shots, or whether a failed capture
 * gets retried — that workflow authority lives in the backend. `totalShots`
 * and `capturedCount` arrive as backend state; `shot_interval_ms` arrives
 * via config. CameraService retries a failed capture internally, so this
 * screen only ever sees one terminal 'completed' or 'failed' event per
 * shot. The backend advances to REVEAL once it has all the shots (which
 * unmounts this screen).
 *
 * Camera events are consumed from the app's single SSE stream (`cameraJob`)
 * passed down as a prop — and are PRESENTATION-ONLY here: 'fired' drives
 * flash/sound, 'completed' the between-shots thumbnail, 'failed' the retry
 * overlay. The FSM guards one-shot-in-flight, so any camera_job event that
 * arrives while this screen is mounted belongs to the current shot — no
 * job-id bookkeeping needed. Camera *health* likewise comes from the backend
 * via `cameraStatus` (SSE camera_status, also surfaced app-wide as a banner)
 * — this screen does not form its own opinion of whether the camera is
 * broken. The only camera fact it owns is `cameraReady`: whether our preview
 * <img> has actually painted a frame, which is ephemeral view state the
 * backend can't observe and only gates when the countdown starts.
 */
export default function CountdownScreen({
  previewUrl,
  totalShots = 1,
  capturedCount = 0,
  fireShot,
  resumePreview,
  standbyPreview,
  cameraJob,
  cameraStatus,
  onCancel,
  config,
  language,
}) {
  const COUNTDOWN_FROM = config?.countdown_duration || 3;
  const flashEnabled = config?.flash_enabled !== false;
  const shotIntervalMs = config?.shot_interval_ms || 1000;
  const [phase, setPhase] = useState('COUNTDOWN'); // COUNTDOWN | POSING | BETWEEN
  const [count, setCount] = useState(COUNTDOWN_FROM);
  const [flashActive, setFlashActive] = useState(false);
  const [lastCapture, setLastCapture] = useState(null);
  const [isCapturing, setIsCapturing] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [sessionStarted, setSessionStarted] = useState(false);
  // Tracks whether the countdown ring video has played to its natural end.
  // Kept independent of `phase`/`isCapturing` so the ring always finishes
  // its own animation rather than being cut off the instant standby/capture
  // starts (which happens on a fixed hardware-driven schedule, not tied to
  // the video's actual remaining runtime).
  const [countdownVideoDone, setCountdownVideoDone] = useState(false);
  // Set when a capture fails permanently (backend already retried once and
  // still failed). The backend never emits a further 'completed'/'failed'
  // event on its own here, so without this the screen would otherwise wait
  // forever for capturedCount to advance.
  const [captureError, setCaptureError] = useState(null);

  // Camera health is owned by the backend and pushed over SSE (camera_status),
  // already surfaced app-wide as a banner in App.jsx. Consume that single source
  // of truth for "is the camera broken" instead of inventing an independent
  // verdict from a timeout that can disagree with the backend (Rule 5).
  const cameraError = !!cameraStatus?.error;

  // View-level failsafe (ephemeral, Rule 5-exempt — same category as
  // cameraReady). The backend owns the health verdict, but it can't observe
  // whether our preview <img> ever actually paints a frame in the browser. The
  // MJPEG stream can stay open with zero frames (camera warming up, or a stall
  // that never crosses the backend's error threshold), in which case onLoad
  // never fires and the backend reports no error — leaving the guest stuck on
  // the loading spinner. If nothing has painted after a grace period (or the
  // <img> hard-errors), fall through to the escape screen so they can bail.
  // This is not a second opinion on camera health; it only guarantees an exit.
  const [previewStalled, setPreviewStalled] = useState(false);
  useEffect(() => {
    if (cameraReady || cameraError) {
      setPreviewStalled(false);
      return;
    }
    const id = setTimeout(() => setPreviewStalled(true), 15000);
    return () => clearTimeout(id);
  }, [cameraReady, cameraError]);

  const pendingTimeouts = useRef([]);
  const timerRef = useRef(null);
  const countdownVideoRef = useRef(null);
  // Last camera_job event already handled (`${job_id}:${status}`) so an SSE
  // re-delivery can't double-fire the flash or the failure overlay.
  const lastHandledJobEvent = useRef(null);
  // Tracks the capturedCount we've already reacted to, so the round-advance
  // effect below only fires once per new shot the backend confirms.
  const lastHandledCount = useRef(capturedCount);
  // Mirrors countdownVideoDone in a ref so onFired (created inside a
  // useCallback that doesn't depend on that state) always reads the current
  // value instead of a stale closure. If the backend's 'fired' event beats
  // the ring's own 'ended' event (real decode/seek latency varies), the
  // flash is deferred here instead of firing over the still-playing ring.
  const countdownVideoDoneRef = useRef(false);
  const pendingFlashRef = useRef(null);
  // Stable MJPEG src — set once on mount, never changes.
  // This ensures exactly ONE backend preview connection for the entire session.
  const previewSrc = useRef(`${previewUrl}?t=${Date.now()}`);

  // Marks the ring as "done" (natural end, or a load/decode error) and
  // flushes any flash/sound that was waiting on it — an error must still
  // unblock the pending flash, or a missing/broken video file would
  // silently swallow the shutter feedback for the rest of the session.
  const handleCountdownVideoDone = useCallback(() => {
    setCountdownVideoDone(true);
    countdownVideoDoneRef.current = true;
    if (pendingFlashRef.current) {
      pendingFlashRef.current();
      pendingFlashRef.current = null;
    }
  }, []);

  const safeTimeout = useCallback((fn, ms) => {
    const id = setTimeout(fn, ms);
    pendingTimeouts.current.push(id);
    return id;
  }, []);

  // Present capture-lifecycle events from the app's central SSE stream.
  // PRESENTATION ONLY: completion reaches the FSM via backend callbacks, so
  // nothing here reports anything back. The FSM guards one-shot-in-flight,
  // so any event arriving while this screen is mounted is the current shot;
  // the (job_id, status) dedupe just absorbs SSE re-deliveries.
  useEffect(() => {
    if (!cameraJob) return;
    const key = `${cameraJob.job_id}:${cameraJob.status}`;
    if (lastHandledJobEvent.current === key) return;
    lastHandledJobEvent.current = key;

    if (cameraJob.status === 'fired') {
      // Flash + sound once the backend confirms the shutter opened — but
      // never before the countdown ring has actually finished playing. If
      // the ring is still mid-playback when 'fired' arrives, defer the
      // flash to the ring's own 'ended' event instead of overlapping it.
      const triggerFlash = () => {
        if (flashEnabled) {
          setFlashActive(true);
          safeTimeout(() => setFlashActive(false), 250);
        }
        playShutterSound();
      };
      if (countdownVideoDoneRef.current) {
        triggerFlash();
      } else {
        pendingFlashRef.current = triggerFlash;
      }
    } else if (cameraJob.status === 'completed') {
      setIsCapturing(false);
      if (cameraJob.filename) {
        setLastCapture(`/photos/${cameraJob.filename}`);
      }
      // Round advancement comes from capturedCount (backend state), not here.
    } else if (cameraJob.status === 'failed') {
      logger.warn('countdown', 'capture_failed', 'Shot capture failed permanently', { error: cameraJob.error });
      setIsCapturing(false);
      setCaptureError(cameraJob.error || true);
    }
  }, [cameraJob, flashEnabled, safeTimeout]);

  // Run a single countdown round
  const runCountdown = useCallback((onDone) => {
    setPhase('COUNTDOWN');
    setCountdownVideoDone(false);
    countdownVideoDoneRef.current = false;
    pendingFlashRef.current = null;
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
      countdownVideoRef.current.play().catch(err =>
        logger.warn('countdown', 'countdown_video_play_fail', 'Countdown ring video failed to play', { error: err.message })
      );
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

  // Fire the shutter for one shot via the FSM (FIRE_SHOT). Everything after
  // this is backend-owned: the camera reports completion straight to the FSM,
  // which bumps capturedCount (round advance) or moves to REVEAL. This screen
  // just hears the presentational camera_job events handled above.
  const fireShutter = useCallback(async () => {
    setIsCapturing(true);
    try {
      await fireShot();
    } catch (err) {
      // The FIRE_SHOT dispatch itself failed (backend unreachable) — no
      // camera_job events will ever come, so surface the retry overlay now.
      logger.warn('countdown', 'fire_shot_fail', 'FIRE_SHOT dispatch failed', { error: err.message });
      setIsCapturing(false);
      setCaptureError(err.message || true);
    }
  }, [fireShot]);

  const startRound = useCallback(async () => {
    // Wake up the camera worker from standby
    if (resumePreview) {
      await resumePreview();
    }

    runCountdown(() => {
      fireShutter();
    });
  }, [runCountdown, fireShutter, resumePreview]);

  const handleRetryShot = useCallback(() => {
    setCaptureError(null);
    startRound();
  }, [startRound]);

  // 1. Mount: wake up the camera. Camera events arrive via props (central SSE).
  useEffect(() => {
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

  // 2. Start session only when the preview has painted and the backend reports
  // the camera healthy.
  useEffect(() => {
    if (cameraReady && !sessionStarted && !cameraError) {
      setSessionStarted(true);
      startRound();
    }
  }, [cameraReady, sessionStarted, startRound, cameraError]);

  // 3. Advance to the next round once the backend confirms a shot landed.
  // Whether more shots are needed is the FSM's call (totalShots/capturedCount
  // are backend state); this screen just reacts to it rather than keeping
  // its own duplicate "are we done" tally.
  useEffect(() => {
    if (capturedCount > lastHandledCount.current) {
      lastHandledCount.current = capturedCount;
      if (capturedCount < totalShots) {
        setPhase('BETWEEN');
        safeTimeout(startRound, shotIntervalMs);
      }
    } else {
      lastHandledCount.current = capturedCount;
    }
  }, [capturedCount, totalShots, shotIntervalMs, safeTimeout, startRound]);


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
          onError={() => {
            logger.warn('countdown', 'preview_load_fail', 'Live-view <img> stream failed to load');
            setPreviewStalled(true);
          }}
          style={{ opacity: cameraReady ? 1 : 0, transition: 'opacity 0.3s' }}
        />

        {/* Error State — backend camera health (cameraStatus) OR the local
            preview-never-painted failsafe. Either way the guest gets an exit. */}
        {(cameraError || previewStalled) && (
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

        {/* Error state for a shot that failed permanently (backend already
            retried once). Without this, the screen would otherwise wait
            forever for a capturedCount bump that will never come. */}
        {captureError && (
          <div className="countdown-loading">
            <div className="countdown-loading-glow" aria-hidden="true" />
            <div className="countdown-loading-content">
              <p className="countdown-loading__kicker" style={{ color: 'var(--error)' }}>
                {t('countdown.captureFailed', language)}
              </p>
              <p className="countdown-loading__sub" style={{ marginTop: '8px' }}>
                {t('countdown.captureFailedSub', language)}
              </p>
              <div style={{ display: 'flex', gap: '1rem', marginTop: '2rem' }}>
                <button className="countdown-btn-home" onClick={handleRetryShot}>
                  <span>{t('reveal.tryAgain', language)}</span>
                </button>
                <button className="countdown-btn-home" onClick={onCancel}>
                  <span className="btn-icon"><Home strokeWidth={1.5} size={20} /></span>
                  <span>{t('reveal.home', language)}</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Loading Spinner for cold start */}
        {!cameraReady && !cameraError && !previewStalled && (
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

        {/* Countdown video - always mounted for performance, toggled via opacity.
            Visibility follows the video's own playback (onEnded), not `phase` —
            standby/capture run on a fixed hardware schedule that shouldn't cut
            the ring's animation short. z-index keeps it below the flash (100). */}
        <div
          className="countdown-center"
          style={{ opacity: !countdownVideoDone ? 1 : 0, transition: 'opacity 0.2s' }}
        >
          <video
            ref={countdownVideoRef}
            src="/countdown.mp4"
            muted
            playsInline
            preload="auto"
            className="countdown-ring-video"
            onEnded={handleCountdownVideoDone}
            onError={handleCountdownVideoDone}
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
              {totalShots - capturedCount === 1
                ? t('countdown.oneMore', language)
                : t('countdown.moreToGo', language).replace('{n}', totalShots - capturedCount)
              }
            </p>
          </div>
        )}



        {/* Progress dots (multi-shot layouts only) */}
        {totalShots > 1 && (
          <div className="countdown-progress">
            <ProgressDots current={capturedCount} total={totalShots} />
          </div>
        )}

      </div>
    </div>
  );
}
