import React, { useState, useEffect, useMemo } from 'react';
import ScreenShell from '../components/ScreenShell';
import PhotoFrame from '../components/PhotoFrame';
import ConfettiOverlay from '../components/ConfettiOverlay';
import Button from '../components/Button';
import { getRandomCompliment } from '../utils/compliments';
import './RevealScreen.css';



/**
 * Photo reveal screen — the emotional peak.
 * Shows the processed photo with confetti and a compliment.
 * Two actions: Retake or Print (→ Frame Picker).
 */
export default function RevealScreen({
  finalPhoto,
  isProcessing,
  retakeCount,
  maxRetakes = 3,
  onRetake,
  onPrint,
}) {
  const compliment = useMemo(() => getRandomCompliment(), [finalPhoto]);
  const [showPhoto, setShowPhoto] = useState(false);
  const isLastRetake = retakeCount >= maxRetakes - 1;
  const canRetake = retakeCount < maxRetakes;

  // Trigger reveal animation after processing completes
  useEffect(() => {
    if (finalPhoto && !isProcessing) {
      const t = setTimeout(() => setShowPhoto(true), 100);
      return () => clearTimeout(t);
    }
    setShowPhoto(false);
  }, [finalPhoto, isProcessing]);

  return (
    <ScreenShell className="reveal-screen">
      {/* Confetti — only when photo is ready */}
      {showPhoto && <ConfettiOverlay />}

      {isProcessing ? (
        /* Loading state */
        <div className="reveal-loading">
          <div className="reveal-spinner" />
          <p className="reveal-loading__text">Creating your keepsake…</p>
        </div>
      ) : finalPhoto ? (
        /* Photo reveal */
        <div className={`reveal-content ${showPhoto ? 'reveal-content--visible' : ''}`}>
          <PhotoFrame
            src={`/photos/${finalPhoto}?t=${Date.now()}`}
            alt="Your photo"
            size="large"
            className="photo-frame--reveal"
          />

          <p className="reveal-compliment">{compliment}</p>

          <div className="reveal-actions">
            {canRetake && (
              <Button variant="ghost" onClick={onRetake}>
                {isLastRetake ? '↺ Last try!' : '↺ Retake'}
              </Button>
            )}
            <Button variant="primary" size="large" glow onClick={onPrint}>
              Print It!
            </Button>
          </div>
        </div>
      ) : (
        <div className="reveal-error">
          <p>Something went wrong. Please try again.</p>
          <Button variant="ghost" onClick={onRetake}>↺ Retake</Button>
        </div>
      )}
    </ScreenShell>
  );
}
