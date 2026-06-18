import React, { useState, useEffect, useCallback, useRef } from 'react';
import useCamera from './hooks/useCamera';
import useApi from './hooks/useApi';
import { logger, startSession, endSession } from './utils/logger';
import { t } from './utils/i18n';

// Screens
import AttractScreen from './screens/AttractScreen';
import ChooseStyleScreen from './screens/ChooseStyleScreen';
import CountdownScreen from './screens/CountdownScreen';
import RevealScreen from './screens/RevealScreen';
import PickFavoriteScreen from './screens/PickFavoriteScreen';
import FramePickerScreen from './screens/FramePickerScreen';
import PrintingScreen from './screens/PrintingScreen';
import DownloadScreen from './screens/DownloadScreen';

// Admin
import AdminPanel from './components/admin/AdminPanel';

// ─── State Machine ──────────────────────────────────────────────
const SCREENS = {
  ATTRACT: 'ATTRACT',
  CHOOSE_STYLE: 'CHOOSE_STYLE',
  COUNTDOWN: 'COUNTDOWN',
  REVEAL: 'REVEAL',
  PICK_FAVORITE: 'PICK_FAVORITE',
  FRAME_PICKER: 'FRAME_PICKER',
  PRINTING: 'PRINTING',
  DOWNLOAD: 'DOWNLOAD',
};

export default function App() {
  const [language, setLanguage] = useState('en');
  const [screen, setScreen] = useState(SCREENS.ATTRACT);
  const [layoutMode, setLayoutMode] = useState('single');
  const [capturedImages, setCapturedImages] = useState([]);
  const [finalPhoto, setFinalPhoto] = useState(null);
  const [retakeCount, setRetakeCount] = useState(0);
  const [allSessionPhotos, setAllSessionPhotos] = useState([]); // tracks ALL processed filenames in this session
  const [isProcessing, setIsProcessing] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [adminTapCount, setAdminTapCount] = useState(0);

  const camera = useCamera();
  const api = useApi();
  const inactivityTimer = useRef(null);
  const adminTapTimer = useRef(null);

  // ─── URL Routing (mobile download) ───
  useEffect(() => {
    const path = window.location.pathname;
    if (path.startsWith('/download/')) {
      setScreen(SCREENS.DOWNLOAD);
    }
  }, []);

  // ─── Camera Error Fallback is handled in render ───

  // ─── Inactivity Timeout ───
  const resetInactivityTimer = useCallback(() => {
    if (inactivityTimer.current) clearTimeout(inactivityTimer.current);

    const timeoutSec = api.config?.session_timeout || 120;
    inactivityTimer.current = setTimeout(() => {
      setScreen((currentScreen) => {
        if (
          currentScreen !== SCREENS.ATTRACT &&
          currentScreen !== SCREENS.DOWNLOAD &&
          !showAdmin
        ) {
          logger.info('session', 'session_timeout', 'Inactivity timeout — resetting to attract');
          endSession('timeout');
          resetSession();
          return SCREENS.ATTRACT;
        }
        return currentScreen;
      });
    }, timeoutSec * 1000);
  }, [api.config, showAdmin]);

  useEffect(() => {
    if (screen === SCREENS.ATTRACT || screen === SCREENS.DOWNLOAD) {
      if (inactivityTimer.current) clearTimeout(inactivityTimer.current);
      return;
    }
    resetInactivityTimer();

    const handleTouch = () => resetInactivityTimer();
    window.addEventListener('pointerdown', handleTouch, { passive: true });
    return () => {
      window.removeEventListener('pointerdown', handleTouch);
      if (inactivityTimer.current) clearTimeout(inactivityTimer.current);
    };
  }, [screen, resetInactivityTimer]);

  // ─── Hidden Admin: 5 rapid taps ───
  const handleBrandingTap = useCallback(() => {
    setAdminTapCount((c) => {
      const next = c + 1;
      if (next >= 5) {
        clearTimeout(adminTapTimer.current);
        setShowAdmin(true);
        return 0;
      }
      clearTimeout(adminTapTimer.current);
      adminTapTimer.current = setTimeout(() => setAdminTapCount(0), 2000);
      return next;
    });
  }, []);

  // ─── Flow Handlers ───

  const handleStart = useCallback(() => {
    startSession();
    logger.info('ui', 'ui_tap_start', 'Guest tapped start');
    setScreen(SCREENS.CHOOSE_STYLE);
  }, []);

  const handleSelectLayout = useCallback((mode) => {
    logger.info('ui', 'ui_select_layout', `Selected ${mode} layout`, { layout: mode });
    setLayoutMode(mode);
    setCapturedImages([]);
    setRetakeCount(0);
    setFinalPhoto(null);
    setAllSessionPhotos([]);
    setScreen(SCREENS.COUNTDOWN);
  }, []);

  const handleCaptureComplete = useCallback(async (images) => {
    logger.info('camera', 'camera_capture', `Captured ${images.length} image(s)`);
    setCapturedImages(images);
    setScreen(SCREENS.REVEAL);
    setIsProcessing(true);
    try {
      const overlayId = api.config?.selected_overlay || 'none';
      const result = await api.savePhoto(images, layoutMode, overlayId);
      setFinalPhoto(result.filename);
      setAllSessionPhotos((prev) => [...prev, { filename: result.filename, rawImages: images }]);
    } catch (err) {
      logger.error('photo', 'photo_process_fail', `Photo processing failed: ${err.message}`, { error: err.message });
      setFinalPhoto(null);
    } finally {
      setIsProcessing(false);
    }
  }, [api, layoutMode]);

  const handleRetake = useCallback(() => {
    setRetakeCount((c) => {
      logger.info('ui', 'ui_retake', `Retake #${c + 1}`);
      return c + 1;
    });
    setCapturedImages([]);
    setFinalPhoto(null);
    setScreen(SCREENS.COUNTDOWN);
  }, []);

  const handlePrintFromReveal = useCallback(() => {
    // If user took multiple photos, let them pick their favorite
    if (allSessionPhotos.length > 1) {
      setScreen(SCREENS.PICK_FAVORITE);
      return;
    }
    // Otherwise go straight to frame picker / printing
    proceedToPrintFlow();
  }, [allSessionPhotos, proceedToPrintFlow]);

  const proceedToPrintFlow = useCallback(() => {
    const overlays = api.config?.overlays || [];
    const hasFrameOptions = overlays.filter((o) => o.id !== 'none').length > 0;
    if (hasFrameOptions && overlays.length > 1) {
      setScreen(SCREENS.FRAME_PICKER);
    } else {
      setScreen(SCREENS.PRINTING);
    }
  }, [api.config]);

  const handleFavoriteSelect = useCallback((selectedFilename) => {
    setFinalPhoto(selectedFilename);
    const sessionInfo = allSessionPhotos.find(p => p.filename === selectedFilename);
    if (sessionInfo) {
      setCapturedImages(sessionInfo.rawImages);
    }
    proceedToPrintFlow();
  }, [allSessionPhotos, proceedToPrintFlow]);

  const handleFrameSelect = useCallback(async (overlayId) => {
    setIsProcessing(true);
    try {
      const result = await api.savePhoto(capturedImages, layoutMode, overlayId);
      setFinalPhoto(result.filename);
      setScreen(SCREENS.PRINTING);
    } catch (err) {
      logger.error('photo', 'frame_apply_fail', `Frame apply failed: ${err.message}`, { error: err.message });
    } finally {
      setIsProcessing(false);
    }
  }, [api, capturedImages, layoutMode]);

  const handleFrameSkip = useCallback(() => {
    setScreen(SCREENS.PRINTING);
  }, []);

  const handleFinish = useCallback(() => {
    logger.info('session', 'session_end', 'Session finished normally');
    endSession('completed');
    resetSession();
  }, []);

  const handleAnother = useCallback(() => {
    logger.info('session', 'session_end', 'Guest chose to take another photo');
    endSession('another');
    resetSession();
    startSession();
    setScreen(SCREENS.CHOOSE_STYLE);
  }, []);

  const resetSession = () => {
    setScreen(SCREENS.ATTRACT);
    setFinalPhoto(null);
    setCapturedImages([]);
    setRetakeCount(0);
    setAllSessionPhotos([]);
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

      {/* ─── Screen Router ─── */}

      {screen === SCREENS.ATTRACT && (
        <AttractScreen 
          config={api.config} 
          onStart={handleStart} 
          language={language}
          setLanguage={setLanguage}
        />
      )}

      {screen === SCREENS.CHOOSE_STYLE && (
        <ChooseStyleScreen 
          onSelect={handleSelectLayout} 
          onBack={() => setScreen(SCREENS.ATTRACT)}
          language={language}
        />
      )}

      {screen === SCREENS.COUNTDOWN && (
        <CountdownScreen
          previewUrl={camera.previewUrl}
          layoutMode={layoutMode}
          captureFrame={camera.captureFrame}
          onComplete={handleCaptureComplete}
          config={api.config}
          language={language}
        />
      )}

      {screen === SCREENS.REVEAL && (
        <RevealScreen
          finalPhoto={finalPhoto}
          isProcessing={isProcessing}
          retakeCount={retakeCount}
          maxRetakes={api.config?.max_photos_per_session || 5}
          onRetake={handleRetake}
          onPrint={handlePrintFromReveal}
          onCancel={resetSession}
          language={language}
        />
      )}

      {screen === SCREENS.PICK_FAVORITE && (
        <PickFavoriteScreen
          allPhotos={allSessionPhotos.map(p => p.filename)}
          onSelect={handleFavoriteSelect}
          onBack={handleFinish}
          isProcessing={isProcessing}
          language={language}
        />
      )}

      {screen === SCREENS.FRAME_PICKER && (
        <FramePickerScreen
          finalPhoto={finalPhoto}
          overlays={api.config?.overlays || []}
          currentOverlay={api.config?.selected_overlay || 'none'}
          onSelect={handleFrameSelect}
          onSkip={handleFrameSkip}
          onBack={handleFinish}
          isProcessing={isProcessing}
          language={language}
        />
      )}

      {screen === SCREENS.PRINTING && (
        <PrintingScreen
          finalPhoto={finalPhoto}
          printPhoto={api.printPhoto}
          getQrUrl={api.getQrUrl}
          getDownloadUrl={api.getDownloadUrl}
          config={api.config}
          onFinish={handleFinish}
          onAnother={handleAnother}
          language={language}
        />
      )}

      {screen === SCREENS.DOWNLOAD && (
        <DownloadScreen filename={downloadFilename} language={language} />
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
          <h2 className="offline-title">{t('offline.title', language) || 'Reconnecting...'}</h2>
          <p className="offline-text">{t('offline.text', language) || 'The photo booth will be back in a moment.'}</p>
        </div>
      )}

      {/* ─── Admin Trigger (branding tap) ─── */}
      {screen !== SCREENS.DOWNLOAD && screen !== SCREENS.COUNTDOWN && !showAdmin && (
        <button
          className="admin-trigger"
          onClick={handleBrandingTap}
          aria-label="Admin"
        >
          L'Étoile
        </button>
      )}

      {/* ─── Admin Panel (full-page) ─── */}
      {showAdmin && (
        <AdminPanel
          config={api.config}
          onSave={handleAdminSave}
          onClose={() => setShowAdmin(false)}
          getDiagnostics={api.getDiagnostics}
          emergencyAction={api.emergencyAction}
          changePin={api.changePin}
          getRecentLogs={api.getRecentLogs}
        />
      )}

      {camera.cameraStatus.error && (
        <div style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          backgroundColor: 'rgba(200, 16, 46, 0.95)',
          color: 'white',
          textAlign: 'center',
          padding: '12px 20px',
          fontFamily: 'var(--font-body)',
          fontWeight: 500,
          zIndex: 9999,
          boxShadow: '0 -4px 12px rgba(0,0,0,0.15)',
          backdropFilter: 'blur(4px)'
        }}>
          ⚠️ Camera Disconnected: {camera.cameraStatus.error}. Please check the USB connection to the Canon M50.
        </div>
      )}
    </div>
  );
}
