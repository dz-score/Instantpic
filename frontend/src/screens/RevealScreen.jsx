import React, { useState, useEffect, useRef } from 'react';
import ScreenShell from '../components/ScreenShell';
import ConfettiOverlay from '../components/ConfettiOverlay';
import { t } from '../utils/i18n';
import useTapGuard from '../hooks/useTapGuard';
import { RotateCcw, Heart, Home } from 'lucide-react';
import '../components/PhotoCrop.css';
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
  layoutMode = 'single',
}) {
  const [showPhoto, setShowPhoto] = useState(false);
  const [guard, armed] = useTapGuard();
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

          {/* Heading */}
          <h1 className="reveal-title">{t('reveal.title', language)}</h1>

          {/* Photo — cropped to just the picture; the cream matte and
              names/date caption on the print composite are hidden here. */}
          <div className="reveal-photo-wrap">
            <div className={`reveal-photo photo-crop photo-crop--${layoutMode}`}>
              <img
                src={`/photos/${finalPhoto}?t=${cacheKey.current}`}
                alt="Your photo"
                className="photo-crop__img"
              />
            </div>
          </div>

          {/* Actions */}
          <div className="reveal-actions">
            {canRetake && (
              <button className="reveal-btn-retake" onClick={guard(onRetake)} disabled={!armed}>
                <span className="reveal-btn-retake__icon btn-icon"><RotateCcw strokeWidth={1.5} size={20} /></span>
                <span className="reveal-btn-retake__main">
                  {isLastRetake ? t('reveal.lastTry', language) : t('reveal.retake', language)}
                </span>
              </button>
            )}
            <button className="reveal-btn-print" onClick={guard(onPrint)} disabled={!armed}>
              <span className="reveal-btn-print__icon btn-icon"><Heart strokeWidth={1.5} size={36} /></span>
              <span className="reveal-btn-print__main">{t('reveal.loveIt', language)}</span>
            </button>
          </div>

        </div>
      ) : (
        /* ── Error State ── */
        <div className="reveal-error">
          <p className="reveal-error__text">{t('reveal.error', language)}</p>
          <div className="reveal-actions" style={{ marginTop: '2rem' }}>
            <button className="reveal-btn-retake" onClick={guard(onRetake)} disabled={!armed}>
              <span className="reveal-btn-retake__icon btn-icon"><RotateCcw strokeWidth={1.5} size={20} /></span>
              <span className="reveal-btn-retake__main">{t('reveal.retakeMain', language)}</span>
            </button>
            <button className="reveal-btn-retake" onClick={guard(onCancel)} disabled={!armed} style={{ background: 'var(--bg-card)' }}>
              <span className="reveal-btn-retake__icon btn-icon"><Home strokeWidth={1.5} size={20} /></span>
              <span className="reveal-btn-retake__main">{t('reveal.home', language)}</span>
            </button>
          </div>
        </div>
      )}
    </ScreenShell>
  );
}
