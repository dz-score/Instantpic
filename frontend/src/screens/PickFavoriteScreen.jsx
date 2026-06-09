import React, { useState } from 'react';
import ScreenShell from '../components/ScreenShell';
import { t } from '../utils/i18n';
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
  language,
}) {
  const [selected, setSelected] = useState(allPhotos.length - 1);

  return (
    <ScreenShell className="pick-fav-screen">

      {/* ── Back to Home (top-left) ── */}
      {onBack && (
        <button className="pick-fav__home" onClick={onBack}>
          <span className="pick-fav__home-icon">⌂</span>
          <span className="pick-fav__home-text">{t('framePicker.home', language)}</span>
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
      <p className="pick-fav__kicker">{t('pickFavorite.kicker', language)}</p>
      <h1 className="pick-fav__title">{t('pickFavorite.title', language)}</h1>
      <span className="pick-fav__heart" aria-hidden="true">♥</span>

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

      {/* ── Actions ── */}
      <div className="pick-fav__actions">
        <button
          className="pick-fav__confirm"
          onClick={() => onSelect(allPhotos[selected])}
          disabled={isProcessing}
        >
          <span className="pick-fav__confirm-icon">✓</span>
          <span className="pick-fav__confirm-text">
            {isProcessing ? t('pickFavorite.processing', language) : t('pickFavorite.confirm', language)}
          </span>
        </button>
      </div>

    </ScreenShell>
  );
}
