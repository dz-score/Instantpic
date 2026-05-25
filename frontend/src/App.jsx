import React, { useState, useEffect, useCallback } from 'react';
import useCamera from './hooks/useCamera';
import useApi from './hooks/useApi';

// Screens
import AttractScreen from './screens/AttractScreen';
import ChooseStyleScreen from './screens/ChooseStyleScreen';
import CountdownScreen from './screens/CountdownScreen';
import RevealScreen from './screens/RevealScreen';
import FramePickerScreen from './screens/FramePickerScreen';
import PrintingScreen from './screens/PrintingScreen';
import DownloadScreen from './screens/DownloadScreen';

// Components
import AdminModal from './components/AdminModal';

// ─── State Machine ──────────────────────────────────────────────
const SCREENS = {
  ATTRACT: 'ATTRACT',
  CHOOSE_STYLE: 'CHOOSE_STYLE',
  COUNTDOWN: 'COUNTDOWN',
  REVEAL: 'REVEAL',
  FRAME_PICKER: 'FRAME_PICKER',
  PRINTING: 'PRINTING',
  DOWNLOAD: 'DOWNLOAD',
};

export default function App() {
  const [screen, setScreen] = useState(SCREENS.ATTRACT);
  const [layoutMode, setLayoutMode] = useState('single');
  const [capturedImages, setCapturedImages] = useState([]);
  const [finalPhoto, setFinalPhoto] = useState(null);
  const [retakeCount, setRetakeCount] = useState(0);
  const [isProcessing, setIsProcessing] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [adminTapCount, setAdminTapCount] = useState(0);

  const camera = useCamera();
  const api = useApi();

  // ─── URL Routing (mobile download) ───
  useEffect(() => {
    const path = window.location.pathname;
    if (path.startsWith('/download/')) {
      setScreen(SCREENS.DOWNLOAD);
    }
  }, []);

  // ─── Init Camera ───
  useEffect(() => {
    camera.initCamera();
    return () => camera.stopCamera();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ─── Keep video src in sync ───
  useEffect(() => {
    camera.ensureVideoSrc();
  }, [screen, camera]);

  // ─── Hidden Admin: 5 rapid taps ───
  const handleBrandingTap = useCallback(() => {
    setAdminTapCount((c) => {
      const next = c + 1;
      if (next >= 5) {
        setShowAdmin(true);
        return 0;
      }
      // Reset after 2 seconds of inactivity
      setTimeout(() => setAdminTapCount(0), 2000);
      return next;
    });
  }, []);

  // ─── Flow Handlers ───

  const handleStart = useCallback(() => {
    setScreen(SCREENS.CHOOSE_STYLE);
  }, []);

  const handleSelectLayout = useCallback((mode) => {
    setLayoutMode(mode);
    setCapturedImages([]);
    setRetakeCount(0);
    setFinalPhoto(null);
    setScreen(SCREENS.COUNTDOWN);
  }, []);

  const handleCaptureComplete = useCallback(async (images) => {
    setCapturedImages(images);
    setScreen(SCREENS.REVEAL);
    setIsProcessing(true);
    try {
      const overlayId = api.config?.selected_overlay || 'none';
      const result = await api.savePhoto(images, layoutMode, overlayId);
      setFinalPhoto(result.filename);
    } catch (err) {
      console.error('Photo processing failed:', err);
      setFinalPhoto(null);
    } finally {
      setIsProcessing(false);
    }
  }, [api, layoutMode]);

  const handleRetake = useCallback(() => {
    setRetakeCount((c) => c + 1);
    setCapturedImages([]);
    setFinalPhoto(null);
    setScreen(SCREENS.COUNTDOWN);
  }, []);

  const handlePrintFromReveal = useCallback(() => {
    const overlays = api.config?.overlays || [];
    const hasFrameOptions = overlays.filter((o) => o.id !== 'none').length > 0;
    if (hasFrameOptions && overlays.length > 1) {
      setScreen(SCREENS.FRAME_PICKER);
    } else {
      setScreen(SCREENS.PRINTING);
    }
  }, [api.config]);

  const handleFrameSelect = useCallback(async (overlayId) => {
    setIsProcessing(true);
    try {
      const result = await api.savePhoto(capturedImages, layoutMode, overlayId);
      setFinalPhoto(result.filename);
      setScreen(SCREENS.PRINTING);
    } catch (err) {
      console.error('Frame apply failed:', err);
    } finally {
      setIsProcessing(false);
    }
  }, [api, capturedImages, layoutMode]);

  const handleFrameSkip = useCallback(() => {
    setScreen(SCREENS.PRINTING);
  }, []);

  const handleFinish = useCallback(() => {
    resetSession();
  }, []);

  const handleAnother = useCallback(() => {
    resetSession();
    setScreen(SCREENS.CHOOSE_STYLE);
  }, []);

  const resetSession = () => {
    setScreen(SCREENS.ATTRACT);
    setFinalPhoto(null);
    setCapturedImages([]);
    setRetakeCount(0);
    setIsProcessing(false);
    setLayoutMode('single');
  };

  const handleAdminSave = useCallback(async (updates) => {
    await api.saveConfig(updates);
  }, [api]);

  // ─── Mobile download route ───
  const downloadFilename = window.location.pathname.startsWith('/download/')
    ? window.location.pathname.replace('/download/', '')
    : null;

  // ─── Render ───
  return (
    <div className="app-root" style={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden' }}>

      {/* Persistent camera <video> — always mounted, hidden when not on countdown */}
      <video
        ref={camera.videoRef}
        autoPlay
        playsInline
        muted
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '1px',
          height: '1px',
          opacity: 0,
          pointerEvents: 'none',
        }}
      />

      {/* ─── Screen Router ─── */}

      {screen === SCREENS.ATTRACT && (
        <AttractScreen
          config={api.config}
          gallery={api.gallery}
          onStart={handleStart}
        />
      )}

      {screen === SCREENS.CHOOSE_STYLE && (
        <ChooseStyleScreen
          onSelect={handleSelectLayout}
          onBack={() => setScreen(SCREENS.ATTRACT)}
        />
      )}

      {screen === SCREENS.COUNTDOWN && (
        <CountdownScreen
          videoRef={camera.videoRef}
          layoutMode={layoutMode}
          captureFrame={camera.captureFrame}
          onComplete={handleCaptureComplete}
        />
      )}

      {screen === SCREENS.REVEAL && (
        <RevealScreen
          finalPhoto={finalPhoto}
          isProcessing={isProcessing}
          retakeCount={retakeCount}
          onRetake={handleRetake}
          onPrint={handlePrintFromReveal}
        />
      )}

      {screen === SCREENS.FRAME_PICKER && (
        <FramePickerScreen
          finalPhoto={finalPhoto}
          overlays={api.config?.overlays || []}
          currentOverlay={api.config?.selected_overlay || 'none'}
          onSelect={handleFrameSelect}
          onSkip={handleFrameSkip}
          isProcessing={isProcessing}
        />
      )}

      {screen === SCREENS.PRINTING && (
        <PrintingScreen
          finalPhoto={finalPhoto}
          printPhoto={api.printPhoto}
          getQrUrl={api.getQrUrl}
          onFinish={handleFinish}
          onAnother={handleAnother}
        />
      )}

      {screen === SCREENS.DOWNLOAD && (
        <DownloadScreen filename={downloadFilename} />
      )}

      {/* ─── Offline Overlay ─── */}
      {!api.isOnline && screen !== SCREENS.DOWNLOAD && (
        <div className="offline-overlay">
          <div className="offline-icon">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <circle cx="12" cy="12" r="10" />
              <path d="M15 9l-6 6M9 9l6 6" />
            </svg>
          </div>
          <h2 className="offline-title">Reconnecting…</h2>
          <p className="offline-text">The photo booth will be back in a moment.</p>
        </div>
      )}

      {/* ─── Admin Trigger (branding tap) ─── */}
      {screen !== SCREENS.DOWNLOAD && screen !== SCREENS.COUNTDOWN && (
        <button
          className="admin-trigger"
          onClick={handleBrandingTap}
          aria-label="Admin"
        >
          L'Étoile
        </button>
      )}

      {/* ─── Admin Modal ─── */}
      {showAdmin && (
        <AdminModal
          config={api.config}
          onSave={handleAdminSave}
          onClose={() => setShowAdmin(false)}
        />
      )}
    </div>
  );
}
