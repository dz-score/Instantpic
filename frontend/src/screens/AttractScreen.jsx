import React, { useCallback } from 'react';
import ScreenShell from '../components/ScreenShell';
import { unlockAudio } from '../utils/sounds';
import './AttractScreen.css';

/**
 * Welcome / attract screen — the first thing guests see.
 *
 * Layout (top → bottom):
 *   - Decorative hearts + flourish
 *   - "WELCOME TO OUR" (small caps, gold)
 *   - Couple names (large ornamental script, gold)
 *   - "PHOTO BOOTH" (spaced caps, gold)
 *   - Heart divider
 *   - Welcome message (italic, tracked)
 *   - Large blush CTA pill: "♡ TAP TO START THE FUN!"
 *
 * Background comes from ScreenShell (bg-wedding.png).
 */
export default function AttractScreen({ config, onStart }) {

  const handleTap = useCallback(() => {
    unlockAudio();
    onStart();
  }, [onStart]);

  const coupleNames = config?.couple_names || 'Welcome';
  const welcomeMsg = config?.welcome_message || 'Capture the love. Create memories.';

  return (
    <ScreenShell className="attract-screen">
      {/* Full-screen tap target */}
      <button className="attract-content" onClick={handleTap}>

        {/* ── Decorative top flourish ── */}
        <div className="attract-flourish" aria-hidden="true">
          <span className="attract-flourish__hearts">♡♡</span>
          <div className="attract-flourish__line">
            <span className="attract-flourish__curl">❧</span>
            <span className="attract-flourish__dash" />
            <span className="attract-flourish__curl attract-flourish__curl--flip">❧</span>
          </div>
        </div>

        {/* ── Heading block ── */}
        <p className="attract-kicker">Welcome to our</p>
        <h1 className="attract-names">{coupleNames}</h1>
        <p className="attract-label">— Photo Booth —</p>

        {/* ── Heart divider ── */}
        <span className="attract-heart" aria-hidden="true">♥</span>

        {/* ── Subtitle ── */}
        <p className="attract-subtitle">{welcomeMsg}</p>

        {/* ── CTA Button ── */}
        <div className="attract-cta">
          <span className="attract-cta__shimmer" aria-hidden="true" />
          <span className="attract-cta__icon">♡</span>
          <span className="attract-cta__text">Tap to Start the Fun!</span>
        </div>

      </button>
    </ScreenShell>
  );
}
