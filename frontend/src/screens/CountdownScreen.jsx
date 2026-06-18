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
  const pendingTimeouts = useRef([]);
  const imagesRef = useRef([]);
  const totalShots = layoutMode === 'collage' ? 3 : 1;
  const timerRef = useRef(null);
  const countdownVideoRef = useRef(null);

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
      } else {
        clearInterval(timerRef.current);
        onDone();
      }
    }, 1000);
  }, [COUNTDOWN_FROM]);

  // Fire shutter: flash + sound + capture (with auto-retry)
  const fireShutter = useCallback(async () => {
    setIsCapturing(true);
    if (flashEnabled) {
      setFlashActive(true);
      safeTimeout(() => setFlashActive(false), 250);
    }
    playShutterSound();

    let frame = await captureFrame();
    
    // Auto-retry once if capture failed (handles transient camera errors)
    if (!frame) {
      console.warn('[CountdownScreen] Capture failed, retrying in 1.5s...');
      await new Promise(r => setTimeout(r, 1500));
      frame = await captureFrame();
    }
    
    setIsCapturing(false);
    
    if (frame) {
      imagesRef.current = [...imagesRef.current, frame];
      setLastCapture(`/photos/${frame}`); // frame is the filename
    }

    return frame;
  }, [captureFrame, flashEnabled, safeTimeout]);

  const startRound = useCallback((idx) => {
    setShotIndex(idx);
    runCountdown(async () => {
      await fireShutter();
      if (idx + 1 >= totalShots) {
        // All shots taken — small delay then send results
        safeTimeout(() => {
          onComplete(imagesRef.current);
        }, 400);
      } else {
        // Show between-shots interstitial
        setPhase('BETWEEN');
        safeTimeout(() => {
          startRound(idx + 1);
        }, BETWEEN_SHOT_DELAY);
      }
    });
  }, [runCountdown, fireShutter, totalShots, safeTimeout, onComplete]);

  // Orchestrate the full session
  useEffect(() => {
    imagesRef.current = [];
    setShotIndex(0);
    startRound(0);

    return () => {
      clearInterval(timerRef.current);
      pendingTimeouts.current.forEach(clearTimeout);
      pendingTimeouts.current = [];
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);


  return (
    <div className="countdown-screen">
      {/* Camera feed — full bleed */}
      <div className="countdown-viewport">
        {/* We use a cache-busting query param so the browser doesn't cache the MJPEG stream */}
        {!isCapturing && (
          <img
            src={`${previewUrl}?t=${Date.now()}`}
            className="countdown-video"
            alt="Camera Live View"
          />
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
        
        {isCapturing && (
          <div className="countdown-between">
            <p className="countdown-between__text">{t('framePicker.applying', language) || "Capturing..."}</p>
          </div>
        )}

        {/* Progress dots (collage only) */}
        {layoutMode === 'collage' && (
          <div className="countdown-progress">
            <ProgressDots current={shotIndex} total={totalShots} />
          </div>
        )}
      </div>
    </div>
  );
}
