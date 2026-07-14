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
  cameraJob,
  cameraStatus,
  onCancel,
  config,
  language,
}) {
  // Numbers shown, and how fast they tick. The guest sees COUNTDOWN_FROM
  // numbers either way; SPEED just compresses them. 5 @ 1.25 = a full
  // 5,4,3,2,1 in 4.0s. See backend/config.py — the effective length feeds the
  // shot-spacing budget that keeps the next shot inside the M50's live-view
  // window, and speeding the count buys that time without dropping a number.
  const COUNTDOWN_FROM = config?.countdown_duration || 3;
  const COUNTDOWN_SPEED = config?.countdown_speed || 1;
  // The tick and the decrement are the same quantity in different units:
  // 0.25s of countdown per tick. Scaling the tick interval scales the whole
  // countdown; the ring video's playbackRate is scaled by the same factor
  // below so the animation stays in step with the numbers.
  const TICK_MS = 250 / COUNTDOWN_SPEED;
  // Where the ring video must sit to show COUNTDOWN_FROM as its first frame.
  // The 10s video places number N at (10 - N) + 0.1 — so "9" is at 1.1s, "3" at
  // 7.1s, and "1" at 9.1s, which is where it RUNS OUT. There is no "0" frame.
  const ringStartTime = COUNTDOWN_FROM >= 10 ? 0 : Math.max(0, (10 - COUNTDOWN_FROM) + 0.1);
  const flashEnabled = config?.flash_enabled !== false;
  const shotIntervalMs = config?.shot_interval_ms || 1000;
  const [phase, setPhase] = useState('COUNTDOWN'); // COUNTDOWN | POSING | BETWEEN
  const [count, setCount] = useState(COUNTDOWN_FROM);
  const [flashActive, setFlashActive] = useState(false);
  const [isCapturing, setIsCapturing] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [sessionStarted, setSessionStarted] = useState(false);
  // Tracks whether the countdown ring video has played to its natural end.
  // Kept independent of `phase`/`isCapturing` so the ring always finishes
  // its own animation rather than being cut off the instant standby/capture
  // starts (which happens on a fixed hardware-driven schedule, not tied to
  // the video's actual remaining runtime).
  const [countdownVideoDone, setCountdownVideoDone] = useState(false);
  // Whether the ring video has finished seeking to this round's start offset.
  // Gates its visibility: shown before the seek lands, it displays the frame it
  // was parked on — the previous round's trailing "1".
  const [ringSeeked, setRingSeeked] = useState(false);
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
  // The current round's completion callback (fireShutter). Held in a ref so the
  // tick — which is started from the video's 'playing' event, not from
  // runCountdown's closure — can read it without going stale.
  const onDoneRef = useRef(null);
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
  // Park the ring on the frame this round must OPEN with, so that when the round
  // starts there is nothing to seek and nothing to wait for.
  //
  // Two bugs, one cause. A <video> keeps painting its last frame, and seeking is
  // asynchronous while the JS countdown is not:
  //   - end of a round leaves it on "1" (the video runs out there — there is no
  //     "0" frame), so round 2+ flashed "1" before jumping to 5;
  //   - a fresh mount leaves it at 0, so the FIRST round of a session had to seek
  //     all the way to ringStartTime while the timer was already running. The ring
  //     then trailed ~1s behind and the shutter fired while it still showed "2"
  //     (booth report, 3s countdown: "the first shot is too early").
  // Parking ahead of time — on mount, on metadata load, and at the end of every
  // round — means the frame it is sitting on is always already the right one.
  const parkRing = useCallback(() => {
    const v = countdownVideoRef.current;
    if (!v) return;
    try {
      v.pause();
      v.currentTime = ringStartTime;
    } catch {
      // Seeking throws if metadata isn't loaded yet; onLoadedMetadata re-parks.
    }
  }, [ringStartTime]);

  // Park on mount and whenever the countdown length changes (admin panel). The
  // screen sits on the camera warmup for 1-3.5s before the first round starts,
  // which is ample time for the seek to land.
  useEffect(() => {
    parkRing();
  }, [parkRing]);

  const handleCountdownVideoDone = useCallback(() => {
    setCountdownVideoDone(true);
    countdownVideoDoneRef.current = true;
    parkRing();   // hidden right now — re-park for the next round
    if (pendingFlashRef.current) {
      pendingFlashRef.current();
      pendingFlashRef.current = null;
    }
  }, [parkRing]);

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
      // Round advancement comes from capturedCount (backend state), not here.
    } else if (cameraJob.status === 'failed') {
      logger.warn('countdown', 'capture_failed', 'Shot capture failed permanently', { error: cameraJob.error });
      setIsCapturing(false);
      setCaptureError(cameraJob.error || true);
    }
  }, [cameraJob, flashEnabled, safeTimeout]);

  // Advance the numeric countdown, one tick at a time. Started from the video's
  // own 'playing' event (see runCountdown), NOT in parallel with play(), so the
  // numbers and the ring animation run off a single clock. The guard on timerRef
  // means the 'playing' event and the safety-net fallback in runCountdown can't
  // both start it; onDoneRef carries the round's completion callback because this
  // runs as a bare event handler with no closure over the round.
  const startTick = useCallback(() => {
    if (timerRef.current) return;   // already ticking this round
    let c = COUNTDOWN_FROM;
    setCount(c);
    timerRef.current = setInterval(() => {
      c -= 0.25;
      if (c > 0) {
        if (c % 1 === 0) setCount(c);
      } else {
        // Countdown done — fire straight away.
        //
        // There used to be an extra 250ms beat here, during which the frontend
        // called standbyPreview() to "let the USB bus settle" before the heavy
        // capture command. The backend has owned that since it became
        // capture-authoritative: camera_service sets _capture_in_progress at
        // enqueue, before the job is even queued, with its own 15ms settle.
        // So the beat bought nothing — it only froze the live view 250ms early
        // and padded the shot-to-shot gap, which is the gap that decides
        // whether the next shot lands inside the M50's healthy live-view
        // window (~6s after the previous capture).
        clearInterval(timerRef.current);
        timerRef.current = null;
        setPhase('POSING');
        const onDone = onDoneRef.current;
        onDoneRef.current = null;
        if (onDone) onDone();
      }
    }, TICK_MS);
  }, [COUNTDOWN_FROM, TICK_MS]);

  // Run a single countdown round.
  const runCountdown = useCallback((onDone) => {
    setPhase('COUNTDOWN');
    setCountdownVideoDone(false);
    countdownVideoDoneRef.current = false;
    pendingFlashRef.current = null;
    onDoneRef.current = onDone;
    // Drop any stale interval so startTick's guard reflects this round only.
    clearInterval(timerRef.current);
    timerRef.current = null;
    setCount(COUNTDOWN_FROM);

    // Start the ring. handleCountdownVideoDone already parked it on ringStartTime
    // when the last round ended, so it is normally sitting on the right frame
    // and needs no seek at all — in which case NO 'seeked' event will fire, and
    // gating visibility on that event would hide the ring for the whole fallback.
    // So: if it is already parked, show it immediately; only gate when we
    // genuinely have to seek (first round after mount, or a config change).
    const v = countdownVideoRef.current;
    if (v) {
      v.playbackRate = COUNTDOWN_SPEED;
      if (Math.abs(v.currentTime - ringStartTime) < 0.05) {
        setRingSeeked(true);
      } else {
        setRingSeeked(false);
        v.currentTime = ringStartTime;   // async — onSeeked reveals the ring
        safeTimeout(() => setRingSeeked(true), 400);  // ...or this does, if it never fires
      }
      // The numeric tick starts from the video's 'playing' event (onPlaying),
      // not here, so both begin on the same real instant. The first play() of a
      // session pays a decode/seek cost of tens-to-hundreds of ms; starting the
      // tick in parallel let that latency desync the two, leaving the ring on
      // "1" while POSING/"Smile" (and the deferred shutter flash) came up over
      // it — the reported first-shot overlap. The 500ms fallback re-arms the
      // tick if 'playing' never fires (missing/broken video) so capture can't
      // stall waiting on a ring that will never play.
      v.play().catch(err => {
        logger.warn('countdown', 'countdown_video_play_fail', 'Countdown ring video failed to play', { error: err.message });
        startTick();   // playback won't happen — run the numbers on their own
      });
      safeTimeout(startTick, 500);
    } else {
      // No video element at all — run the numbers on the JS clock alone.
      startTick();
    }
  }, [COUNTDOWN_SPEED, ringStartTime, safeTimeout, startTick]);

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

        {/* The camera is physically taking the photo during POSING: capture and
            live view share one USB pipe, so the preview is frozen for ~1.75s
            and no code can unfreeze it (only decoupling live view would).
            Name the moment so the freeze reads as "we're shooting" rather than
            as a hung app — the guest sees the ring hit 1 and the picture stop. */}
        {phase === 'POSING' && !captureError && (
          <div className="countdown-posing">
            <p className="countdown-posing__text">{t('countdown.smile', language)}</p>
          </div>
        )}

        {/* Countdown video - always mounted for performance, toggled via opacity.
            Shown only while phase === COUNTDOWN: the numbers and the ring now
            share one clock (the tick starts from the video's own 'playing'
            event), so the ring reaches its end just as the tick completes. The
            phase gate is the hard guarantee on top of that timing — the instant
            the count hits zero and phase flips to POSING, the ring is hidden, so
            it is structurally impossible for it to paint over "Smile"/the flash
            even if onEnded is late or dropped. z-index keeps it below the flash
            (100). */}
        <div
          className="countdown-center"
          style={{ opacity: phase === 'COUNTDOWN' && !countdownVideoDone && ringSeeked ? 1 : 0, transition: 'opacity 0.2s' }}
        >
          <video
            ref={countdownVideoRef}
            src="/countdown.mp4"
            muted
            playsInline
            preload="auto"
            className="countdown-ring-video"
            onLoadedMetadata={parkRing}
            onPlaying={startTick}
            onSeeked={() => setRingSeeked(true)}
            onEnded={handleCountdownVideoDone}
            onError={handleCountdownVideoDone}
          />
        </div>

        {/* Between shots: a big "get ready" prompt, deliberately text-only.
            This used to also show the shot just taken — but that <img> pointed
            at the raw capture straight off the camera (~7MB, 24MP), and
            decoding it into a 240x160 box blocked the browser's main thread
            for seconds. That starved the countdown's setInterval, stretching a
            5s countdown to 8.5s and freezing the UI mid-collage while the ring
            video (compositor-driven) kept spinning. The guest sees every shot
            on the reveal screen anyway, so the thumbnail bought nothing. */}
        {phase === 'BETWEEN' && !isCapturing && (
          <div className="countdown-between">
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
