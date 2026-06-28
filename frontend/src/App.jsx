import React, { useState, useEffect, useCallback, useRef } from 'react';
import useSse from './hooks/useSse';
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

export default function App() {
  const [language, setLanguage] = useState('en');
  const [showAdmin, setShowAdmin] = useState(false);
  const [adminTapCount, setAdminTapCount] = useState(0);
  const [appState, setAppState] = useState(null);

  const sse = useSse();
  const camera = useCamera(sse.cameraStatus);
  const api = useApi(sse.isOnline);
  const inactivityTimer = useRef(null);
  const adminTapTimer = useRef(null);

  // Sync state from SSE or Initial Fetch
  useEffect(() => {
    if (sse.backendState) {
      setAppState(sse.backendState);
    }
  }, [sse.backendState]);

  useEffect(() => {
    api.fetchState().then(state => {
      if (state) setAppState(prev => prev || state);
    });
  }, [api]);

  // ─── URL Routing (mobile download) ───
  // We still handle /download/ locally since it's just a static page
  const downloadFilename = window.location.pathname.startsWith('/download/')
    ? window.location.pathname.replace('/download/', '')
    : null;

  const currentScreen = downloadFilename ? 'DOWNLOAD' : (appState?.screen || 'LOADING');

  // ─── Inactivity Timeout ───
  const resetInactivityTimer = useCallback(() => {
    if (inactivityTimer.current) clearTimeout(inactivityTimer.current);

    const timeoutSec = api.config?.session_timeout || 120;
    inactivityTimer.current = setTimeout(() => {
      if (
        currentScreen !== 'ATTRACT' &&
        currentScreen !== 'DOWNLOAD' &&
        currentScreen !== 'LOADING' &&
        !showAdmin
      ) {
        logger.info('session', 'session_timeout', 'Inactivity timeout — backend will reset');
        api.sendEvent('TIMEOUT');
      }
    }, timeoutSec * 1000);
  }, [api, showAdmin, currentScreen]);

  useEffect(() => {
    if (currentScreen === 'ATTRACT' || currentScreen === 'DOWNLOAD' || currentScreen === 'LOADING') {
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
  }, [currentScreen, resetInactivityTimer]);

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

  // ─── Flow Handlers (Backend Driven) ───
  const handleStart = useCallback(() => {
    startSession();
    logger.info('ui', 'ui_tap_start', 'Guest tapped start');
    api.sendEvent('START_SESSION');
  }, [api]);

  const handleSelectLayout = useCallback((mode) => {
    logger.info('ui', 'ui_select_layout', `Selected ${mode} layout`, { layout: mode });
    api.sendEvent('SELECT_LAYOUT', { mode });
  }, [api]);

  const handleCaptureComplete = useCallback((images) => {
    logger.info('camera', 'camera_capture', `Captured ${images.length} image(s)`);
    if (!images || images.length === 0) {
      logger.error('photo', 'photo_process_fail', 'No images captured — all capture attempts failed');
      return;
    }
    
    const cfg = api.config || {};
    const showNames = cfg.show_names_on_photo !== false;
    const text = showNames
      ? ([cfg.couple_names, cfg.event_date].filter(Boolean).join(' · ') || cfg.default_text || '')
      : '';
      
    api.sendEvent('CAPTURE_DONE', {
      images,
      text,
      overlay_id: cfg.selected_overlay || 'none'
    });
  }, [api]);

  const handleRetake = useCallback(() => {
    logger.info('ui', 'ui_retake', 'Retake requested');
    api.sendEvent('RETAKE');
  }, [api]);

  const handlePrintFromReveal = useCallback(() => {
    api.sendEvent('PRINT_FROM_REVEAL', { overlays: api.config?.overlays || [] });
  }, [api]);

  const handleFavoriteSelect = useCallback((selectedFilename) => {
    api.sendEvent('FAVORITE_SELECT', { 
      filename: selectedFilename, 
      overlays: api.config?.overlays || [] 
    });
  }, [api]);

  const handleFrameSelect = useCallback((overlayId) => {
    const cfg = api.config || {};
    const showNames = cfg.show_names_on_photo !== false;
    const text = showNames
      ? ([cfg.couple_names, cfg.event_date].filter(Boolean).join(' · ') || cfg.default_text || '')
      : '';
    api.sendEvent('FRAME_SELECT', { overlay_id: overlayId, text });
  }, [api]);

  const handleFrameSkip = useCallback(() => {
    api.sendEvent('FRAME_SKIP');
  }, [api]);

  const handleFinish = useCallback(() => {
    logger.info('session', 'session_end', 'Session finished normally');
    endSession('completed');
    api.sendEvent('FINISH');
  }, [api]);

  const handleAnother = useCallback(() => {
    logger.info('session', 'session_end', 'Guest chose to take another photo');
    endSession('another');
    startSession();
    api.sendEvent('ANOTHER');
  }, [api]);

  const handleAdminSave = useCallback(async (updates) => {
    await api.saveConfig(updates);
  }, [api]);

  // ─── Render ───
  return (
    <div className="app-root" style={{ position: 'relative', width: '100vw', height: '100vh', overflow: 'hidden' }}>

      {/* ─── Screen Router ─── */}
      
      {currentScreen === 'LOADING' && (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: '#fff' }}>
          <h2>Connecting...</h2>
        </div>
      )}

      {currentScreen === 'ATTRACT' && (
        <AttractScreen 
          config={api.config} 
          onStart={handleStart} 
          language={language}
          setLanguage={setLanguage}
        />
      )}

      {currentScreen === 'CHOOSE_STYLE' && (
        <ChooseStyleScreen 
          onSelect={handleSelectLayout} 
          onBack={() => api.sendEvent('FINISH')}
          language={language}
        />
      )}

      {currentScreen === 'COUNTDOWN' && (
        <CountdownScreen
          previewUrl={camera.previewUrl}
          layoutMode={appState?.layoutMode || 'single'}
          captureFrame={camera.captureFrame}
          resumePreview={camera.resumePreview}
          standbyPreview={camera.standbyPreview}
          onComplete={handleCaptureComplete}
          onCancel={() => api.sendEvent('FINISH')}
          config={api.config}
          language={language}
        />
      )}

      {currentScreen === 'REVEAL' && (
        <RevealScreen
          finalPhoto={appState?.finalPhoto}
          isProcessing={appState?.isProcessing || false}
          retakeCount={appState?.retakeCount || 0}
          maxRetakes={api.config?.max_photos_per_session || 5}
          onRetake={handleRetake}
          onPrint={handlePrintFromReveal}
          onCancel={() => api.sendEvent('FINISH')}
          language={language}
        />
      )}

      {currentScreen === 'PICK_FAVORITE' && (
        <PickFavoriteScreen
          allPhotos={(appState?.allSessionPhotos || []).map(p => p.filename)}
          onSelect={handleFavoriteSelect}
          onBack={handleFinish}
          isProcessing={appState?.isProcessing || false}
          language={language}
        />
      )}

      {currentScreen === 'FRAME_PICKER' && (
        <FramePickerScreen
          finalPhoto={appState?.finalPhoto}
          overlays={api.config?.overlays || []}
          currentOverlay={api.config?.selected_overlay || 'none'}
          onSelect={handleFrameSelect}
          onSkip={handleFrameSkip}
          onBack={handleFinish}
          isProcessing={appState?.isProcessing || false}
          language={language}
        />
      )}

      {currentScreen === 'PRINTING' && (
        <PrintingScreen
          finalPhoto={appState?.finalPhoto}
          printPhoto={api.printPhoto}
          getQrUrl={api.getQrUrl}
          getDownloadUrl={api.getDownloadUrl}
          config={api.config}
          onFinish={handleFinish}
          onAnother={handleAnother}
          language={language}
        />
      )}

      {currentScreen === 'DOWNLOAD' && (
        <DownloadScreen filename={downloadFilename} language={language} />
      )}

      {/* ─── Offline Overlay ─── */}
      {!api.isOnline && currentScreen !== 'DOWNLOAD' && (
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
      {currentScreen !== 'DOWNLOAD' && currentScreen !== 'COUNTDOWN' && !showAdmin && (
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
          cameraStatus={camera.cameraStatus}
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
