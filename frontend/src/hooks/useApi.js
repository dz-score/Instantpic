import { useState, useEffect, useCallback } from 'react';
import { logger } from '../utils/logger';

const API = '';

/**
 * Centralises all API interactions.
 *
 * Config is NOT fetched here — it is pushed by the backend over SSE (see
 * useSse) on connect and on every change, so the frontend always has a fresh,
 * self-healing copy. This hook only handles writes (saveConfig, changePin) and
 * other REST calls.
 */
export default function useApi(isOnline) {
  const [boothBaseUrl, setBoothBaseUrl] = useState('');

  /* ── Fetch booth LAN IP on mount ── */
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API}/api/network-info`);
        const data = await r.json();
        setBoothBaseUrl(data.base_url || '');
      } catch {
        // Fallback: use current origin (works for same-device access)
        setBoothBaseUrl(window.location.origin);
      }
    })();
  }, []);

  /* ── Initial State ── */
  const fetchState = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/state`);
      return await r.json();
    } catch (e) {
      logger.error('api', 'api_error', `Failed to load state: ${e.message}`);
      return null;
    }
  }, []);

  /* ── Events ── */
  const sendEvent = useCallback(async (type, payload = {}) => {
    try {
      const r = await fetch(`${API}/api/events`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type, payload }),
      });
      if (!r.ok) throw new Error('Event submission failed');
      return await r.json();
    } catch (e) {
      logger.error('api', 'api_event_error', `Failed to send event ${type}: ${e.message}`, { type, error: e.message });
      throw e;
    }
  }, []);

  /* ── Save config (admin) ── */
  const saveConfig = useCallback(async (updates) => {
    const r = await fetch(`${API}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    if (!r.ok) throw new Error('Config save failed');
    // The backend broadcasts the updated config over SSE, so no local set here.
    return await r.json();
  }, []);

  /* ── QR / download helpers ── */
  const getDownloadUrl = useCallback((filename) => {
    const base = boothBaseUrl || window.location.origin;
    return `${base}/download/${filename}`;
  }, [boothBaseUrl]);

  const getQrUrl = useCallback((downloadUrl) => {
    return `${API}/api/qrcode?text=${encodeURIComponent(downloadUrl)}`;
  }, []);

  /* ── Diagnostics ── */
  const getDiagnostics = useCallback(async () => {
    const r = await fetch(`${API}/api/diagnostics`);
    if (!r.ok) throw new Error('Diagnostics failed');
    return await r.json();
  }, []);

  /* ── LED ring ── */
  const testLed = useCallback(async () => {
    const r = await fetch(`${API}/api/led/test`, { method: 'POST' });
    const body = await r.json();
    // 409 means the booth is mid-session, which is a real answer rather than a
    // failure — the backend refuses to queue a diagnostic behind the shutter.
    if (!r.ok) return { ok: false, detail: body.detail || 'Test failed' };
    return body;
  }, []);

  const testLedChannel = useCallback(async (channel) => {
    const r = await fetch(`${API}/api/led/channel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel }),
    });
    const body = await r.json();
    // 409 (booth busy, ring disabled) and 400 (bad channel) are real answers,
    // not transport failures — surface the backend's reason rather than a
    // generic one.
    if (!r.ok) return { ok: false, channel, detail: body.detail || 'Test failed' };
    return body;
  }, []);

  /* ── Emergency actions ── */
  const emergencyAction = useCallback(async (action) => {
    const r = await fetch(`${API}/api/emergency`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action }),
    });
    if (!r.ok) throw new Error('Emergency action failed');
    return await r.json();
  }, []);

  /* ── Change PIN ── */
  const changePin = useCallback(async (currentPin, newPin) => {
    const r = await fetch(`${API}/api/change-pin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_pin: currentPin, new_pin: newPin }),
    });
    if (!r.ok) {
      const err = await r.json();
      throw new Error(err.detail || 'PIN change failed');
    }
    // Backend broadcasts the updated config (incl. new PIN) over SSE.
    return await r.json();
  }, []);

  /* ── Recent Logs ── */
  const getRecentLogs = useCallback(async (count = 50, source = 'both') => {
    const r = await fetch(`${API}/api/logs/recent?count=${count}&source=${source}`);
    if (!r.ok) throw new Error('Failed to fetch logs');
    return await r.json();
  }, []);

  /* ── Camera preview wake-up ──
   * The countdown screen wakes the backend preview worker from standby on
   * mount / round start. Capture itself is NOT triggered here: the shutter
   * fires only via the FSM (FIRE_SHOT). */
  const resumeCameraPreview = useCallback(async () => {
    try {
      logger.info('camera', 'resume_start', 'Waking up backend camera worker');
      await fetch(`${API}/api/camera/resume`, { method: 'POST' });
    } catch (err) {
      logger.error('camera', 'resume_fail', `Failed to resume camera: ${err.message}`);
    }
  }, []);

  /* ── Camera settings (admin) ──
   * Live gphoto2 EXIF settings are read/written directly (not part of the SSE
   * config broadcast) because they are a USB round-trip to the camera. */
  const getCameraConfig = useCallback(async () => {
    const r = await fetch(`${API}/api/camera/config`);
    if (!r.ok) throw new Error('Failed to fetch camera settings');
    return await r.json();
  }, []);

  const saveCameraConfig = useCallback(async (settings) => {
    const r = await fetch(`${API}/api/camera/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
    });
    if (!r.ok) throw new Error('Failed to update setting');
    return await r.json();
  }, []);

  return {
    isOnline,
    saveConfig,
    getQrUrl,
    getDownloadUrl,
    getDiagnostics,
    testLed,
    testLedChannel,
    emergencyAction,
    changePin,
    getRecentLogs,
    getCameraConfig,
    saveCameraConfig,
    resumeCameraPreview,
    fetchState,
    sendEvent,
  };
}
