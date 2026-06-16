import React, { useState, useRef } from 'react';
import ScreenShell from '../components/ScreenShell';
import { t } from '../utils/i18n';
import { Home, Printer } from 'lucide-react';
import './FramePickerScreen.css';

/**
 * Frame selection screen — shown before printing.
 *
 * Layout (matching mockup):
 *   - "Back to Home" pill (top-left)
 *   - Decorative flourish + hearts
 *   - "MAKE IT YOURS" kicker
 *   - "Choose Your Frame" (large gold script)
 *   - Subtitle
 *   - Left: large photo preview with gold border
 *   - Right: "CHOOSE A FRAME" section header + frame thumbnail cards
 *   - Bottom: blush "Print & Share" + gold "Retake Photo" buttons
 *   - Footer text
 */
export default function FramePickerScreen({
  finalPhoto,
  overlays,
  currentOverlay,
  onSelect,
  onSkip,
  onBack,
  isProcessing,
  language,
}) {
  const [selected, setSelected] = useState(currentOverlay || 'none');
  const cacheKey = useRef(Date.now());
  const selectedOverlay = overlays.find((o) => o.id === selected) || null;

  const handleConfirm = () => {
    if (selected !== currentOverlay) {
      onSelect(selected);
    } else {
      onSkip();
    }
  };

  return (
    <ScreenShell className="frame-screen">

      {/* ── Back to Home (top-left) ── */}
      {onBack && (
        <button className="frame__home" onClick={onBack}>
          <span className="frame__home-icon btn-icon"><Home strokeWidth={1.5} size={20} /></span>
          <span className="frame__home-text">{t('framePicker.home', language)}</span>
        </button>
      )}

      {/* ── Decorative flourish ── */}
      <div className="frame__flourish" aria-hidden="true">
        <span className="frame__flourish-hearts">♡♡</span>
        <div className="frame__flourish-line">
          <span className="frame__flourish-curl">❧</span>
          <span className="frame__flourish-dash" />
          <span className="frame__flourish-curl frame__flourish-curl--flip">❧</span>
        </div>
      </div>

      {/* ── Heading ── */}
      <p className="frame__kicker">{t('framePicker.kicker', language)}</p>
      <h1 className="frame__title">{t('framePicker.title', language)}</h1>

      {/* ── Main content: preview + frame cards ── */}
      <div className="frame__body">

        {/* Large photo preview with live overlay */}
        <div className="frame__preview-wrap">
          <div className="frame__preview">
            <img
              src={`/photos/${finalPhoto}?t=${cacheKey.current}`}
              alt="Your photo"
              className="frame__preview-img"
            />
            {/* Live overlay preview */}
            {selectedOverlay && selectedOverlay.filename && (
              <img
                src={`/overlays/${selectedOverlay.filename}`}
                alt=""
                className="frame__preview-overlay"
              />
            )}
          </div>
        </div>

        {/* Frame options panel */}
        <div className="frame__options">

          <div className="frame__cards">
            {overlays.map((overlay) => (
              <button
                key={overlay.id}
                className={`frame__card ${selected === overlay.id ? 'frame__card--selected' : ''}`}
                onClick={() => setSelected(overlay.id)}
                disabled={isProcessing}
              >
                <div className="frame__card-preview">
                  {overlay.filename ? (
                    <img
                      src={`/overlays/${overlay.filename}`}
                      alt=""
                      className="frame__card-overlay"
                    />
                  ) : (
                    /* No frame — show empty frame outline */
                    <div className="frame__card-empty">
                      <div className="frame__card-empty-inner" />
                    </div>
                  )}
                </div>
                {selected === overlay.id && (
                  <span className="frame__card-check">✓</span>
                )}
                <span className="frame__card-name">{overlay.name}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Actions ── */}
      <div className="frame__actions">
        <button
          className="frame__btn-print"
          onClick={handleConfirm}
          disabled={isProcessing}
        >
          <span className="btn-icon"><Printer strokeWidth={1.5} size={24} /></span>
          <span className="frame__btn-print-main">
            {isProcessing ? t('framePicker.applying', language) : t('framePicker.printBtn', language)}
          </span>
        </button>
      </div>

    </ScreenShell>
  );
}
