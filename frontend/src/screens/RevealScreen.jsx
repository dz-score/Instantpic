import React, { useState, useEffect, useMemo, useRef } from 'react';
import ScreenShell from '../components/ScreenShell';
import PhotoFrame from '../components/PhotoFrame';
import ConfettiOverlay from '../components/ConfettiOverlay';
import { getRandomCompliment } from '../utils/compliments';
import { t } from '../utils/i18n';
import { RotateCcw, Heart, Home } from 'lucide-react';
import './RevealScreen.css';

/**
 * Photo reveal screen — the emotional peak.
 *
 * Layout (wedding theme):
 *   - Decorative flourish + hearts
 *   - "YOUR MOMENT" kicker
 *   - "Beautifully Captured" (large gold script)
 *   - Large photo preview with gold border
 *   - Compliment text (gold italic)
 *   - Blush "Print It!" button + ghost "Retake" button
 *   - Retake count indicator
 *   - Confetti overlay when photo appears
 *
 * Background from ScreenShell (bg-wedding.png).
 */
export default function RevealScreen({
  finalPhoto,
  isProcessing,
  retakeCount,
  maxRetakes = 3,
  onRetake,
  onPrint,
  onCancel,
  language,
}) {
  const compliment = useMemo(() => getRandomCompliment(language), [finalPhoto, language]);
  const [showPhoto, setShowPhoto] = useState(false);
  const cacheKey = useRef(Date.now());
  const isLastRetake = retakeCount >= maxRetakes - 1;
  const canRetake = retakeCount < maxRetakes;

  useEffect(() => {
    if (finalPhoto && !isProcessing) {
      const t = setTimeout(() => setShowPhoto(true), 100);
      return () => clearTimeout(t);
    }
    setShowPhoto(false);
  }, [finalPhoto, isProcessing]);

  return (
    <ScreenShell className="reveal-screen">
      {/* Confetti */}
      {showPhoto && <ConfettiOverlay />}

      {isProcessing ? (
        /* ── Loading State ── */
        <div className="reveal-loading">
          <div className="reveal-spinner" />
          <p className="reveal-loading__kicker">{t('reveal.creatingKeepsake', language)}</p>
          <p className="reveal-loading__sub">{t('reveal.justAMoment', language)}</p>
        </div>
      ) : finalPhoto ? (
        /* ── Photo Reveal ── */
        <div className={`reveal-content ${showPhoto ? 'reveal-content--visible' : ''}`}>

          {/* Flourish */}
          <div className="reveal-flourish" aria-hidden="true">
            <span className="reveal-flourish__hearts">♡♡</span>
            <div className="reveal-flourish__line">
              <span className="reveal-flourish__curl">❧</span>
              <span className="reveal-flourish__dash" />
              <span className="reveal-flourish__curl reveal-flourish__curl--flip">❧</span>
            </div>
          </div>

          {/* Heading */}
          <p className="reveal-kicker">{t('reveal.kicker', language)}</p>
          <h1 className="reveal-title">{t('reveal.title', language)}</h1>

          {/* Photo */}
          <div className="reveal-photo-wrap">
            <PhotoFrame
              src={`/photos/${finalPhoto}?t=${cacheKey.current}`}
              alt="Your photo"
              size="large"
              className="photo-frame--reveal"
            />
          </div>

          {/* Compliment */}
          <p className="reveal-compliment">{compliment}</p>

          {/* Actions */}
          <div className="reveal-actions">
            {canRetake && (
              <button className="reveal-btn-retake" onClick={onRetake}>
                <span className="reveal-btn-retake__icon btn-icon"><RotateCcw strokeWidth={1.5} size={20} /></span>
                <span className="reveal-btn-retake__main">
                  {isLastRetake ? t('reveal.lastTry', language) : t('reveal.retake', language)}
                </span>
              </button>
            )}
            <button className="reveal-btn-print" onClick={onPrint}>
              <span className="reveal-btn-print__icon btn-icon"><Heart strokeWidth={1.5} size={24} /></span>
              <span className="reveal-btn-print__main">{t('reveal.loveIt', language)}</span>
            </button>
          </div>

          {/* Retake indicator */}
          {canRetake && (
            <p className="reveal-retake-info">
              {t('reveal.retakeInfo', language).replace('{count}', retakeCount).replace('{max}', maxRetakes)}
            </p>
          )}
        </div>
      ) : (
        /* ── Error State ── */
        <div className="reveal-error">
          <p className="reveal-error__text">{t('reveal.error', language)}</p>
          <div className="reveal-actions" style={{ marginTop: '2rem' }}>
            <button className="reveal-btn-retake" onClick={onRetake}>
              <span className="reveal-btn-retake__icon btn-icon"><RotateCcw strokeWidth={1.5} size={20} /></span>
              <span className="reveal-btn-retake__main">{t('reveal.retakeMain', language)}</span>
            </button>
            <button className="reveal-btn-retake" onClick={onCancel} style={{ background: 'var(--bg-card)' }}>
              <span className="reveal-btn-retake__icon btn-icon"><Home strokeWidth={1.5} size={20} /></span>
              <span className="reveal-btn-retake__main">{t('reveal.home', language)}</span>
            </button>
          </div>
        </div>
      )}
    </ScreenShell>
  );
}
