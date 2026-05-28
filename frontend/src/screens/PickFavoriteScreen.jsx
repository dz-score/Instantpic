import React, { useState } from 'react';
import ScreenShell from '../components/ScreenShell';
import Button from '../components/Button';
import './PickFavoriteScreen.css';

/**
 * Photo selection screen — shown when user has multiple takes.
 * Displays all photos from the session and lets user pick their favorite to print.
 * Clean grid layout, tap to select, gold border on active.
 */
export default function PickFavoriteScreen({
  allPhotos,
  onSelect,
  isProcessing,
}) {
  const [selected, setSelected] = useState(allPhotos.length - 1); // default to latest

  return (
    <ScreenShell className="pick-fav-screen">
      <h2 className="pick-fav__title">Choose Your Favorite</h2>
      <p className="pick-fav__subtitle">Tap the photo you'd like to print</p>

      <div className="pick-fav__grid">
        {allPhotos.map((photo, index) => (
          <button
            key={photo}
            className={`pick-fav__card ${selected === index ? 'pick-fav__card--selected' : ''}`}
            onClick={() => setSelected(index)}
          >
            <img
              src={`/photos/${photo}?t=${Date.now()}`}
              alt={`Photo ${index + 1}`}
              className="pick-fav__img"
            />
            <span className="pick-fav__badge">{index + 1}</span>
            {selected === index && (
              <span className="pick-fav__check">✓</span>
            )}
          </button>
        ))}
      </div>

      <div className="pick-fav__actions">
        <Button
          variant="primary"
          size="large"
          glow
          onClick={() => onSelect(allPhotos[selected])}
          disabled={isProcessing}
        >
          {isProcessing ? 'Processing…' : 'Print This One!'}
        </Button>
      </div>
    </ScreenShell>
  );
}
