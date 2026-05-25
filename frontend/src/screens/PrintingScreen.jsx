import React, { useState, useEffect, useRef, useCallback } from 'react';
import ScreenShell from '../components/ScreenShell';
import PhotoFrame from '../components/PhotoFrame';
import Button from '../components/Button';
import './PrintingScreen.css';

const AUTO_RESET_SECONDS = 25;

/**
 * Two-phase printing screen:
 * Phase 1 — Printing animation with progress
 * Phase 2 — QR code + "Take Another" / auto-reset countdown
 */
export default function PrintingScreen({
  finalPhoto,
  printPhoto,
  getQrUrl,
  onFinish,
  onAnother,
}) {
  const [phase, setPhase] = useState('PRINTING'); // PRINTING | DONE | ERROR
  const [printMsg, setPrintMsg] = useState('Printing your keepsake…');
  const [countdown, setCountdown] = useState(AUTO_RESET_SECONDS);
  const countdownRef = useRef(null);

  // Build download link
  const downloadUrl = `${window.location.origin}/download/${finalPhoto}`;
  const qrSrc = getQrUrl(downloadUrl);

  // Trigger print on mount
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await printPhoto(finalPhoto);
        if (!cancelled) {
          setPhase('DONE');
          setPrintMsg('Your print is on its way!');
        }
      } catch {
        if (!cancelled) {
          setPhase('ERROR');
          setPrintMsg('Printing unavailable — scan the QR to save your photo!');
        }
      }
    })();
    return () => { cancelled = true; };
  }, [finalPhoto, printPhoto]);

  // Start auto-reset countdown once printing phase resolves
  useEffect(() => {
    if (phase === 'PRINTING') return;
    setCountdown(AUTO_RESET_SECONDS);
    countdownRef.current = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          clearInterval(countdownRef.current);
          onFinish();
          return 0;
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(countdownRef.current);
  }, [phase, onFinish]);

  const handleAnother = useCallback(() => {
    clearInterval(countdownRef.current);
    onAnother();
  }, [onAnother]);

  return (
    <ScreenShell className="print-screen">
      {/* Small preview */}
      <PhotoFrame
        src={`/photos/${finalPhoto}`}
        alt="Your photo"
        size="small"
        className="print-preview"
      />

      {/* Phase: Printing */}
      {phase === 'PRINTING' && (
        <div className="print-status">
          <div className="print-spinner" />
          <p className="print-status__msg">{printMsg}</p>
        </div>
      )}

      {/* Phase: Done or Error → show QR */}
      {(phase === 'DONE' || phase === 'ERROR') && (
        <div className="print-complete">
          {/* Status message */}
          <div className={`print-badge print-badge--${phase === 'DONE' ? 'success' : 'error'}`}>
            <span className="print-badge__icon">{phase === 'DONE' ? '✓' : '!'}</span>
            <span className="print-badge__text">{printMsg}</span>
          </div>

          {/* QR Code */}
          <div className="print-qr">
            <div className="print-qr__frame">
              <img src={qrSrc} alt="Scan to download" className="print-qr__img" />
            </div>
            <div className="print-qr__instructions">
              <p className="print-qr__step">1. Connect to WiFi</p>
              <p className="print-qr__step">2. Scan this code to save your photo</p>
            </div>
          </div>

          {/* Actions */}
          <div className="print-actions">
            <Button variant="primary" onClick={handleAnother}>
              Take Another Photo!
            </Button>
          </div>

          {/* Auto-reset countdown */}
          <p className="print-countdown">
            Returning home in {countdown}s…
          </p>
        </div>
      )}
    </ScreenShell>
  );
}
