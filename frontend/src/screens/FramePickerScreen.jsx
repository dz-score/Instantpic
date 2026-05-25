import React, { useState } from 'react';
import ScreenShell from '../components/ScreenShell';
import Button from '../components/Button';
import './FramePickerScreen.css';

/**
 * Optional frame picker — shown before printing.
 * 3 visual thumbnail cards showing the overlay applied over a preview.
 * Users tap a card to select a frame and proceed to print.
 */
export default function FramePickerScreen({
  finalPhoto,
  overlays,
  currentOverlay,
  onSelect,
  onSkip,
  isProcessing,
}) {
  const [selected, setSelected] = useState(currentOverlay || 'none');

  const handleConfirm = () => {
    if (selected !== currentOverlay) {
      onSelect(selected);
    } else {
      onSkip(); // No change, skip re-processing
    }
  };

  return (
    <ScreenShell className="frame-screen">
      <h1 className="frame-title">Pick a Frame</h1>
      <p className="frame-subtitle">Choose a border for your keepsake</p>

      <div className="frame-cards">
        {overlays.map((overlay) => (
          <button
            key={overlay.id}
            className={`frame-card ${selected === overlay.id ? 'frame-card--selected' : ''}`}
            onClick={() => setSelected(overlay.id)}
            disabled={isProcessing}
          >
            <div className="frame-card__preview">
              {/* Show the photo as background */}
              <img
                src={`/photos/${finalPhoto}?t=${Date.now()}`}
                alt=""
                className="frame-card__photo"
              />
              {/* Show the overlay on top */}
              {overlay.filename && (
                <img
                  src={`/overlays/${overlay.filename}`}
                  alt=""
                  className="frame-card__overlay-img"
                />
              )}
            </div>
            <span className="frame-card__name">{overlay.name}</span>
            {selected === overlay.id && (
              <span className="frame-card__check">✓</span>
            )}
          </button>
        ))}
      </div>

      <div className="frame-actions">
        <Button
          variant="primary"
          size="large"
          glow
          onClick={handleConfirm}
          disabled={isProcessing}
        >
          {isProcessing ? 'Applying…' : 'Print with this Frame'}
        </Button>
      </div>
    </ScreenShell>
  );
}
