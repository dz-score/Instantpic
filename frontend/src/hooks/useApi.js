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

  return {
    isOnline,
    saveConfig,
    getQrUrl,
    getDownloadUrl,
    getDiagnostics,
    emergencyAction,
    changePin,
    getRecentLogs,
    fetchState,
    sendEvent,
  };
}
