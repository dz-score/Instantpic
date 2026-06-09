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
  videoRef,
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
  const imagesRef = useRef([]);
  const totalShots = layoutMode === 'collage' ? 3 : 1;
  const timerRef = useRef(null);

  // Run a single countdown round
  const runCountdown = useCallback((onDone) => {
    setPhase('COUNTDOWN');
    let c = COUNTDOWN_FROM;
    setCount(c);

    timerRef.current = setInterval(() => {
      c -= 1;
      if (c > 0) {
        setCount(c);
      } else {
        clearInterval(timerRef.current);
        onDone();
      }
    }, 1000);
  }, []);

  // Fire shutter: flash + sound + capture
  const fireShutter = useCallback(() => {
    if (flashEnabled) {
      setFlashActive(true);
      setTimeout(() => setFlashActive(false), 250);
    }
    playShutterSound();

    const frame = captureFrame();
    imagesRef.current = [...imagesRef.current, frame];
    setLastCapture(frame);

    return frame;
  }, [captureFrame]);

  // Orchestrate the full session
  useEffect(() => {
    imagesRef.current = [];
    setShotIndex(0);
    startRound(0);

    return () => clearInterval(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startRound = (idx) => {
    setShotIndex(idx);
    runCountdown(() => {
      fireShutter();
      if (idx + 1 >= totalShots) {
        // All shots taken — small delay then send results
        setTimeout(() => {
          onComplete(imagesRef.current);
        }, 400);
      } else {
        // Show between-shots interstitial
        setPhase('BETWEEN');
        setTimeout(() => {
          startRound(idx + 1);
        }, BETWEEN_SHOT_DELAY);
      }
    });
  };

  return (
    <div className="countdown-screen">
      {/* Camera feed — full bleed */}
      <div className="countdown-viewport">
        <video
          ref={videoRef}
          className="countdown-video"
          autoPlay
          playsInline
          muted
        />

        {/* Warm overlay tint */}
        <div className="countdown-overlay" />

        {/* Flash effect (warm champagne) */}
        <div className={`countdown-flash ${flashActive ? 'countdown-flash--active' : ''}`} />

        {/* Countdown video or between-shots message */}
        {phase === 'COUNTDOWN' && (
          <div className="countdown-center">
            <video 
              key={`countdown-${shotIndex}`}
              src="/coutndown.webm" 
              autoPlay 
              muted 
              playsInline 
              className="countdown-ring-video" 
            />
          </div>
        )}

        {phase === 'BETWEEN' && (
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
      </div>
    </div>
  );
}
