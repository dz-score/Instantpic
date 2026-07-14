import React, { useState, useRef } from 'react';
import ScreenShell from '../components/ScreenShell';
import { t } from '../utils/i18n';
import useTapGuard from '../hooks/useTapGuard';
import { Home } from 'lucide-react';
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
  const [guard, armed] = useTapGuard();
  const cacheKey = useRef(Date.now());

  return (
    <ScreenShell className="pick-fav-screen">

      {/* ── Back to Home (top-left) ── */}
      {onBack && (
        <button className="pick-fav__home" onClick={guard(onBack)} disabled={!armed}>
          <span className="pick-fav__home-icon btn-icon"><Home strokeWidth={1.5} size={20} /></span>
          <span className="pick-fav__home-text">{t('framePicker.home', language)}</span>
        </button>
      )}

      {/* ── Heading ── */}
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
                src={`/photos/${photo}?t=${cacheKey.current}`}
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
          onClick={guard(() => onSelect(allPhotos[selected]))}
          disabled={!armed || isProcessing}
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
