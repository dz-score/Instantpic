import React, { useState } from 'react';
import ScreenShell from '../components/ScreenShell';
import './PickFavoriteScreen.css';

/**
 * Photo selection screen — shown when user has multiple takes.
 *
 * Layout (matching mockup):
 *   - "Back to Home" pill (top-left)
 *   - Decorative flourish + hearts
 *   - "THAT'S A WRAP!" kicker
 *   - "Choose Your Favorite" (large gold script)
 *   - Subtitle explaining max retakes
 *   - Horizontal photo grid with numbered badges + radio dots
 *   - Camera icon + encouragement text
 *   - Blush "Confirm Selection" button + ghost "Retake Photo" button
 */
export default function PickFavoriteScreen({
  allPhotos,
  onSelect,
  onBack,
  isProcessing,
}) {
  const [selected, setSelected] = useState(allPhotos.length - 1);

  return (
    <ScreenShell className="pick-fav-screen">

      {/* ── Back to Home (top-left) ── */}
      {onBack && (
        <button className="pick-fav__home" onClick={onBack}>
          <span className="pick-fav__home-icon">⌂</span>
          <span className="pick-fav__home-text">Back to Home</span>
        </button>
      )}

      {/* ── Decorative flourish ── */}
      <div className="pick-fav__flourish" aria-hidden="true">
        <span className="pick-fav__flourish-hearts">♡♡</span>
        <div className="pick-fav__flourish-line">
          <span className="pick-fav__flourish-curl">❧</span>
          <span className="pick-fav__flourish-dash" />
          <span className="pick-fav__flourish-curl pick-fav__flourish-curl--flip">❧</span>
        </div>
      </div>

      {/* ── Heading ── */}
      <p className="pick-fav__kicker">♥ That's a Wrap! ♥</p>
      <h1 className="pick-fav__title">Choose Your Favorite</h1>
      <span className="pick-fav__heart" aria-hidden="true">♥</span>
      <p className="pick-fav__subtitle">
        You've reached the max retakes.<br />
        Pick the photo you'd like to keep!
      </p>

      {/* ── Photo grid ── */}
      <div className="pick-fav__grid">
        {allPhotos.map((photo, index) => (
          <div className="pick-fav__item" key={photo}>
            <button
              className={`pick-fav__card ${selected === index ? 'pick-fav__card--selected' : ''}`}
              onClick={() => setSelected(index)}
            >
              <span className="pick-fav__badge">{index + 1}</span>
              <img
                src={`/photos/${photo}?t=${Date.now()}`}
                alt={`Photo ${index + 1}`}
                className="pick-fav__img"
              />
            </button>
            {/* Radio dot */}
            <span className={`pick-fav__radio ${selected === index ? 'pick-fav__radio--active' : ''}`} />
          </div>
        ))}
      </div>

      {/* ── Encouragement ── */}
      <div className="pick-fav__encouragement">
        <div className="pick-fav__cam-line">
          <span className="pick-fav__cam-curl">»»»</span>
          <span className="pick-fav__cam-icon">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </span>
          <span className="pick-fav__cam-curl pick-fav__cam-curl--flip">«««</span>
        </div>
        <p className="pick-fav__tagline">Every smile, every moment, beautifully you.</p>
        <p className="pick-fav__tagline-script">We can't wait for you to see it!</p>
      </div>

      {/* ── Actions ── */}
      <div className="pick-fav__actions">
        <button
          className="pick-fav__confirm"
          onClick={() => onSelect(allPhotos[selected])}
          disabled={isProcessing}
        >
          <span className="pick-fav__confirm-icon">✓</span>
          <span className="pick-fav__confirm-text">
            {isProcessing ? 'Processing…' : 'Confirm Selection'}
          </span>
        </button>
      </div>

    </ScreenShell>
  );
}
