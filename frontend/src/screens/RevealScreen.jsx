import React, { useState, useEffect, useRef } from 'react';
import ScreenShell from '../components/ScreenShell';
import ConfettiOverlay from '../components/ConfettiOverlay';
import { t } from '../utils/i18n';
import useTapGuard from '../hooks/useTapGuard';
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
  rawImages = [],
  previewImages = [],
}) {
  const [showPhoto, setShowPhoto] = useState(false);
  const [loadedCount, setLoadedCount] = useState(0);
  const [guard, armed] = useTapGuard();
  const cacheKey = useRef(Date.now());
  const isLastRetake = retakeCount >= maxRetakes - 1;
  const canRetake = retakeCount < maxRetakes;

  // Show the actual captured photo(s), not the print composite (which bakes in
  // the cream matte + names/date caption). Prefer the backend's screen-sized
  // previews: a raw off the M50 is 24MP and takes the booth's browser 1-2s to
  // decode, which is long enough to show an empty frame. Fall back to the raws,
  // then to the composite, so a failed preview only costs smoothness.
  const previewPhotos = previewImages.length
    ? previewImages
    : (rawImages.length ? rawImages : (finalPhoto ? [finalPhoto] : []));

  // Hold the frame until every shot has painted. Even at preview size the
  // decode isn't free, and revealing a half-filled frame looks worse than
  // waiting: the reveal is the emotional peak, so it arrives all at once.
  const photosReady = previewPhotos.length > 0 && loadedCount >= previewPhotos.length;

  // A new set of photos (retake, or the first reveal) restarts the count.
  useEffect(() => {
    setLoadedCount(0);
  }, [previewPhotos.join(',')]);

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

          {/* Photo — the raw capture(s), with the gold frame around them.
              No matte or names/date caption (that lives on the print). */}
          <div className="reveal-photo-wrap">
            <div
              className={`reveal-photo reveal-photo--${previewPhotos.length > 1 ? 'collage' : 'single'} ${photosReady ? 'reveal-photo--loaded' : ''}`}
            >
              {previewPhotos.map((f, i) => (
                <img
                  key={f}
                  src={`/photos/${f}?t=${cacheKey.current}`}
                  alt="Your photo"
                  className="reveal-photo__img"
                  decoding="async"
                  onLoad={() => setLoadedCount(n => n + 1)}
                  onError={() => setLoadedCount(n => n + 1)}
                />
              ))}
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
