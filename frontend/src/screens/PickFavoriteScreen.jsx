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
  photos = [],
  onSelect,
  onBack,
  isProcessing,
  language,
}) {
  const [selected, setSelected] = useState(photos.length - 1);
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
        {photos.map((photo, index) => {
          // Show the raw capture(s), not the print composite (which bakes in
          // the matte + names/date caption). Fall back to the composite if the
          // raw shots aren't present.
          const raws = (photo.rawImages && photo.rawImages.length)
            ? photo.rawImages
            : [photo.filename];
          return (
            <div className="pick-fav__item" key={photo.filename}>
              <button
                className={`pick-fav__card pick-fav__card--${raws.length > 1 ? 'collage' : 'single'} ${selected === index ? 'pick-fav__card--selected' : ''}`}
                onClick={() => setSelected(index)}
              >
                <span className="pick-fav__badge">{index + 1}</span>
                {raws.length > 1 ? (
                  <div className="pick-fav__collage">
                    {raws.map((f, i) => (
                      <img
                        key={i}
                        src={`/photos/${f}?t=${cacheKey.current}`}
                        alt=""
                        className="pick-fav__collage-img"
                      />
                    ))}
                  </div>
                ) : (
                  <img
                    src={`/photos/${raws[0]}?t=${cacheKey.current}`}
                    alt={`Photo ${index + 1}`}
                    className="pick-fav__img"
                  />
                )}
              </button>
              {/* Radio dot */}
              <span className={`pick-fav__radio ${selected === index ? 'pick-fav__radio--active' : ''}`} />
            </div>
          );
        })}
      </div>

      {/* ── Actions ── */}
      <div className="pick-fav__actions">
        <button
          className="pick-fav__confirm"
          onClick={guard(() => onSelect(photos[selected].filename))}
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
