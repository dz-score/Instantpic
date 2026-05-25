import React, { useEffect, useState, useCallback } from 'react';
import ScreenShell from '../components/ScreenShell';
import { unlockAudio } from '../utils/sounds';
import './AttractScreen.css';

const GOLD_DIVIDER = '✦';

/**
 * Animated idle / attract screen.
 * - Cycles through recent photos as a soft background slideshow
 * - Shows couple names + event date
 * - Full-screen tap target
 * - Ambient floating particles
 */
export default function AttractScreen({ config, gallery, onStart }) {
  const [slideIndex, setSlideIndex] = useState(0);

  // Rotate slideshow every 5 seconds
  useEffect(() => {
    if (gallery.length < 2) return;
    const id = setInterval(() => {
      setSlideIndex((i) => (i + 1) % gallery.length);
    }, 5000);
    return () => clearInterval(id);
  }, [gallery]);

  const handleTap = useCallback(() => {
    unlockAudio(); // browser requires gesture to unlock AudioContext
    onStart();
  }, [onStart]);

  const coupleNames = config?.couple_names || config?.default_text || 'Welcome';
  const eventDate = config?.event_date || '';

  return (
    <ScreenShell className="attract-screen">
      {/* Background slideshow */}
      {gallery.length > 0 && (
        <div className="attract-slideshow" aria-hidden="true">
          {gallery.slice(0, 8).map((photo, i) => (
            <div
              key={photo}
              className={`attract-slide ${i === slideIndex ? 'attract-slide--active' : ''}`}
              style={{ backgroundImage: `url(/photos/${photo})` }}
            />
          ))}
          <div className="attract-slideshow__overlay" />
        </div>
      )}

      {/* Ambient particles */}
      <div className="attract-particles" aria-hidden="true">
        {Array.from({ length: 6 }, (_, i) => (
          <span
            key={i}
            className="attract-particle"
            style={{
              left: `${15 + i * 14}%`,
              top: `${20 + (i % 3) * 25}%`,
              animationDelay: `${i * 0.8}s`,
              fontSize: `${6 + (i % 3) * 4}px`,
            }}
          >
            {GOLD_DIVIDER}
          </span>
        ))}
      </div>

      {/* Main content — tap target */}
      <button className="attract-content" onClick={handleTap}>
        <h1 className="attract-title">Create a Beautiful Memory</h1>
        <div className="attract-divider">
          <span className="attract-divider__line" />
          <span className="attract-divider__star">{GOLD_DIVIDER}</span>
          <span className="attract-divider__line" />
        </div>
        <h2 className="attract-couple">{coupleNames}</h2>
        {eventDate && <p className="attract-date">{eventDate}</p>}
        <div className="attract-cta">
          <span className="attract-cta__ring" />
          <span className="attract-cta__text">Tap anywhere to begin</span>
        </div>
      </button>

      {/* Minimal branding */}
      <div className="attract-branding">
        {GOLD_DIVIDER} L'Étoile {GOLD_DIVIDER}
      </div>
    </ScreenShell>
  );
}
