import React, { useEffect, useRef, useCallback } from 'react';
import ScreenShell from '../components/ScreenShell';
import { t } from '../utils/i18n';
import useTapGuard from '../hooks/useTapGuard';
import { Camera, Printer } from 'lucide-react';
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
 * Phase 2 (DONE): printStatus === 'printed'
 *   - A real completion: the backend waits the job out, so the paper is out
 *   - Photo preview + QR code to download
 *   - Blush "Take Another" button + auto-reset countdown
 *
 * Phase 2 (ERROR): printStatus === 'failed'
 *   - Says plainly that nothing came out, and points at an attendant
 *   - The photo is still safe and still downloadable, so the QR stays
 *   - Offers REPRINT, which the FSM accepts only from 'failed'
 *
 * Phase 2 (SPENT): printStatus === 'skipped'
 *   - The event's print allowance is used up. Nothing failed and nobody needs
 *     fetching, so this reads as an ordinary end to the session, not an error.
 *   - No retry: the FSM would refuse it, and offering it would be a lie.
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
  onReprint,
  language,
}) {
  // Derived directly from backend state — no local success/failure of our own.
  const phase = printStatus === 'printed' ? 'DONE'
    : printStatus === 'failed' ? 'ERROR'
    : printStatus === 'skipped' ? 'SPENT'
    : 'PRINTING';

  const [guard, armed] = useTapGuard();
  const resetTimerRef = useRef(null);
  const onFinishRef = useRef(onFinish);

  useEffect(() => { onFinishRef.current = onFinish; }, [onFinish]);

  const downloadUrl = getDownloadUrl(finalPhoto);
  const qrSrc = getQrUrl(downloadUrl);

  // Auto-return home after a fixed delay (no longer shown as a live countdown).
  // The phase goes with it: this timer is what ends most sessions, so without it
  // every failed print is filed as a session that finished normally, and the
  // logs say the night went fine.
  useEffect(() => {
    if (phase === 'PRINTING') return;
    const outcome = phase === 'DONE' ? 'completed'
      : phase === 'SPENT' ? 'print_skipped'
      : 'print_failed';
    resetTimerRef.current = setTimeout(
      () => onFinishRef.current(outcome), AUTO_RESET_SECONDS * 1000);
    return () => clearTimeout(resetTimerRef.current);
  }, [phase]);

  const handleAnother = useCallback(() => {
    clearTimeout(resetTimerRef.current);
    onAnother();
  }, [onAnother]);

  // Retrying puts printStatus back to 'printing', so the phase effect above
  // clears the auto-reset on its own — the guest does not get sent home
  // halfway through the print they just asked for.
  const handleReprint = useCallback(() => {
    clearTimeout(resetTimerRef.current);
    onReprint();
  }, [onReprint]);

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
      {phase !== 'PRINTING' && (
        <div className="print-done">

          {/* Heading */}
          {phase === 'DONE' ? (
            <>
              <h1 className="print-done__title">{t('printing.doneTitle', language)}</h1>
            </>
          ) : phase === 'SPENT' ? (
            <>
              <p className="print-done__kicker">{t('printing.spentKicker', language)}</p>
              <h1 className="print-done__title">{t('printing.spentTitle', language)}</h1>
              <p className="print-done__error-body">{t('printing.spentBody', language)}</p>
            </>
          ) : (
            <>
              <p className="print-done__kicker print-done__kicker--error">
                {t('printing.failedKicker', language)}
              </p>
              <h1 className="print-done__title">{t('printing.failedTitle', language)}</h1>
              <p className="print-done__error-body">{t('printing.failedBody', language)}</p>
            </>
          )}

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

          {/* Actions */}
          <div className="print-done__actions">
            {phase === 'ERROR' && (
              <button className="print-done__btn-retry" onClick={guard(handleReprint)} disabled={!armed}>
                <span className="print-done__btn-icon btn-icon"><Printer strokeWidth={1.5} size={30} /></span>
                <span className="print-done__btn-main">{t('printing.retryPrint', language)}</span>
              </button>
            )}
            <button className="print-done__btn-another" onClick={guard(handleAnother)} disabled={!armed}>
              <span className="print-done__btn-icon btn-icon"><Camera strokeWidth={1.5} size={34} /></span>
              <span className="print-done__btn-main">{t('printing.takeAnother', language)}</span>
            </button>
          </div>
        </div>
      )}

    </ScreenShell>
  );
}
