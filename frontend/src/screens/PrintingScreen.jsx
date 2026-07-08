import React, { useState, useEffect, useRef, useCallback } from 'react';
import ScreenShell from '../components/ScreenShell';
import { t } from '../utils/i18n';
import useTapGuard from '../hooks/useTapGuard';
import { Camera } from 'lucide-react';
import './PrintingScreen.css';

const AUTO_RESET_SECONDS = 25;

/**
 * Printing & Share screen — the final step.
 *
 * The print itself is backend-owned workflow: the FSM kicks it off on entering
 * PRINTING and pushes the real outcome as `printStatus` over SSE. This screen
 * only projects that state — it never triggers the print or guesses the result
 * from a timer (see Rules 1, 4, 16).
 *
 * Phase 1 (PRINTING):  printStatus === 'printing'
 *   - Animated printer icon + warm patience message
 *
 * Phase 2 (DONE/ERROR): printStatus === 'printed' | 'failed'
 *   - Thank you message (from config)
 *   - Photo preview + QR code to download
 *   - Blush "Take Another" button + auto-reset countdown
 *
 * Background from ScreenShell (bg-wedding.png).
 */
export default function PrintingScreen({
  finalPhoto,
  printStatus,
  getQrUrl,
  getDownloadUrl,
  config,
  onFinish,
  onAnother,
  language,
}) {
  // Derived directly from backend state — no local success/failure of our own.
  const phase = printStatus === 'printed' ? 'DONE'
    : printStatus === 'failed' ? 'ERROR'
    : 'PRINTING';

  const [countdown, setCountdown] = useState(AUTO_RESET_SECONDS);
  const [guard, armed] = useTapGuard();
  const countdownRef = useRef(null);
  const onFinishRef = useRef(onFinish);

  useEffect(() => { onFinishRef.current = onFinish; }, [onFinish]);

  const downloadUrl = getDownloadUrl(finalPhoto);
  const qrSrc = getQrUrl(downloadUrl);

  // Auto-reset countdown
  useEffect(() => {
    if (phase === 'PRINTING') return;
    setCountdown(AUTO_RESET_SECONDS);
    countdownRef.current = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          clearInterval(countdownRef.current);
          onFinishRef.current();
          return 0;
        }
        return c - 1;
      });
    }, 1000);
    return () => clearInterval(countdownRef.current);
  }, [phase]);

  const handleAnother = useCallback(() => {
    clearInterval(countdownRef.current);
    onAnother();
  }, [onAnother]);

  return (
    <ScreenShell className="print-screen">

      {/* ── Phase: Printing ── */}
      {phase === 'PRINTING' && (
        <div className="print-loading">
          {/* Animated printer icon */}
          <div className="print-loading__icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <rect x="6" y="14" width="12" height="8" rx="1" />
              <path d="M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2" />
              <path d="M6 9V2h12v7" />
            </svg>
          </div>

          <div className="print-loading__flourish">
            <span className="print-loading__curl">❧</span>
            <span className="print-loading__dash" />
            <span className="print-loading__curl print-loading__curl--flip">❧</span>
          </div>

          <h1 className="print-loading__title">{t('printing.printMemory', language)}</h1>
          <p className="print-loading__sub">{t('printing.justAMoment', language)}</p>

          <div className="print-loading__dots">
            <span className="print-loading__dot" />
            <span className="print-loading__dot" />
            <span className="print-loading__dot" />
          </div>
        </div>
      )}

      {/* ── Phase: Done / Error ── */}
      {(phase === 'DONE' || phase === 'ERROR') && (
        <div className="print-done">

          {/* Flourish */}
          <div className="print-done__flourish" aria-hidden="true">
            <span className="print-done__hearts">♡♡</span>
            <div className="print-done__flourish-line">
              <span className="print-done__curl">❧</span>
              <span className="print-done__dash" />
              <span className="print-done__curl print-done__curl--flip">❧</span>
            </div>
          </div>

          {/* Heading */}
          {phase === 'DONE' ? (
            <>
              <p className="print-done__kicker">{t('printing.doneKicker', language)}</p>
              <h1 className="print-done__title">{t('printing.doneTitle', language)}</h1>
            </>
          ) : (
            <>
              <p className="print-done__kicker">{t('printing.almostThereKicker', language)}</p>
              <h1 className="print-done__title">{t('printing.savePhotoTitle', language)}</h1>
            </>
          )}

          {/* Thank you */}
          <p className="print-done__thankyou">
            {config?.thank_you_message || 'Thank you for celebrating with us!'}
          </p>

          {/* Photo + QR side by side */}
          <div className="print-done__body">
            {/* Photo preview */}
            <div className="print-done__photo-wrap">
              <img
                src={`/photos/${finalPhoto}`}
                alt="Your photo"
                className="print-done__photo"
              />
            </div>

            {/* Instructions Section */}
            <div className="print-done__instructions">
              
              <div className="print-done__inst-card">
                <div className="print-done__inst-header">
                  <span className="print-done__inst-num">1</span>
                  <span className="print-done__inst-title">{t('printing.step1', language).replace('{wifiName}', '').trim()}</span>
                </div>
                <div className="print-done__inst-body">
                  <span className="print-done__wifi-pill">{config?.wifi_network_name || "Gravity Booth"}</span>
                </div>
              </div>

              <div className="print-done__inst-card">
                <div className="print-done__inst-header">
                  <span className="print-done__inst-num">2</span>
                  <span className="print-done__inst-title">{t('printing.step2', language)}</span>
                </div>
                <div className="print-done__inst-body">
                  <div className="print-done__qr-frame">
                    <img src={qrSrc} alt="Scan to download" className="print-done__qr-img" />
                  </div>
                </div>
              </div>

            </div>
          </div>

          {/* Camera divider */}
          <div className="print-done__cam-line" aria-hidden="true">
            <span className="print-done__cam-curl">»»»</span>
            <span className="print-done__cam-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </span>
            <span className="print-done__cam-curl">«««</span>
          </div>

          {/* Actions */}
          <div className="print-done__actions">
            <button className="print-done__btn-another" onClick={guard(handleAnother)} disabled={!armed}>
              <span className="print-done__btn-icon btn-icon"><Camera strokeWidth={1.5} size={34} /></span>
              <span className="print-done__btn-main">{t('printing.takeAnother', language)}</span>
            </button>
          </div>

          {/* Countdown */}
          <p className="print-done__countdown">
            {t('printing.returning', language).replace('{countdown}', countdown)}
          </p>
        </div>
      )}

    </ScreenShell>
  );
}
