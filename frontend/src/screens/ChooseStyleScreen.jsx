import React from 'react';
import ScreenShell from '../components/ScreenShell';
import './ChooseStyleScreen.css';

/**
 * Mode selection — Single Classic vs 3-Photo Collage.
 *
 * Layout:
 *   - Decorative hearts + flourish (top)
 *   - "CHOOSE YOUR" (small caps)
 *   - "Photo Experience" (large script, gold)
 *   - Subtitle
 *   - Two large cards side by side:
 *     - Circle icon badge on top
 *     - Preview image inside (polaroid / strip)
 *     - Title + subtitle below
 *   - Back button (blush pill)
 *
 * Background from ScreenShell (bg-wedding.png).
 */
export default function ChooseStyleScreen({ onSelect, onBack }) {
  return (
    <ScreenShell className="choose-screen">

      {/* ── Decorative top flourish ── */}
      <div className="choose-flourish" aria-hidden="true">
        <span className="choose-flourish__hearts">♡</span>
        <div className="choose-flourish__line">
          <span className="choose-flourish__curl">❧</span>
          <span className="choose-flourish__dash" />
          <span className="choose-flourish__curl choose-flourish__curl--flip">❧</span>
        </div>
      </div>

      {/* ── Heading ── */}
      <p className="choose-kicker">Choose Your</p>
      <h1 className="choose-title">Photo Experience</h1>
      <span className="choose-title__heart" aria-hidden="true">♥</span>
      <p className="choose-subtitle">Pick your favorite way to capture memories!</p>

      {/* ── Cards ── */}
      <div className="choose-cards">

        {/* Single Photo Card */}
        <button className="choose-card" onClick={() => onSelect('single')}>
          <div className="choose-card__badge">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="12" cy="12" r="3" />
              <path d="M3 16l5-5 4 4 3-3 6 6" />
            </svg>
          </div>
          <div className="choose-card__preview">
            <img src="/preview-single.png" alt="Single photo" className="choose-card__img" />
          </div>
          <h2 className="choose-card__title">Single Classic</h2>
          <p className="choose-card__desc">One Beautiful Photo</p>
        </button>

        {/* Collage Strip Card */}
        <button className="choose-card" onClick={() => onSelect('collage')}>
          <div className="choose-card__badge">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="4" y="2" width="16" height="6" rx="1" />
              <rect x="4" y="9" width="16" height="6" rx="1" />
              <rect x="4" y="16" width="16" height="6" rx="1" />
            </svg>
          </div>
          <div className="choose-card__preview">
            <img src="/preview-collage.png" alt="Photo strip" className="choose-card__img" />
          </div>
          <h2 className="choose-card__title">3-Photo Collage</h2>
          <p className="choose-card__desc">Three Moments, One Strip</p>
        </button>

      </div>

      {/* ── Back Button ── */}
      <button className="choose-back" onClick={onBack}>
        <span className="choose-back__icon">⌂</span>
        <span className="choose-back__text">Back to Home</span>
      </button>

    </ScreenShell>
  );
}
