import { useState, useEffect, useCallback, useRef } from 'react';
import { logger } from '../utils/logger';

const API = '';

/**
 * Centralises all API interactions + health-check polling.
 */
export default function useApi(isOnline) {
  const [config, setConfig] = useState(null);
  const [gallery, setGallery] = useState([]);
  const [boothBaseUrl, setBoothBaseUrl] = useState('');
  const configRef = useRef(null);

  /* ── Load config on mount ── */
  const fetchConfig = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/config`);
      const data = await r.json();
      setConfig(data);
      configRef.current = data;
      return data;
    } catch (e) {
      logger.error('api', 'api_error', `Failed to load config: ${e.message}`, { endpoint: '/api/config', error: e.message });
      return null;
    }
  }, []);

  useEffect(() => { fetchConfig(); }, [fetchConfig]);

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

  /* ── Gallery ── */
  const fetchGallery = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/photos`);
      const data = await r.json();
      setGallery(data);
      return data;
    } catch (e) {
      logger.warn('api', 'api_error', `Failed to load gallery: ${e.message}`, { endpoint: '/api/photos' });
      return [];
    }
  }, []);

  // Gallery fetched on-demand after print, not on mount

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

  /* ── Print ── */
  const printPhoto = useCallback(async (filename) => {
    logger.info('printer', 'printer_sent', `Print requested: ${filename}`, { filename });
    const r = await fetch(`${API}/api/print/${filename}`, { method: 'POST' });
    if (!r.ok) {
      logger.error('printer', 'printer_fail', `Print failed: ${filename}`, { filename });
      throw new Error('Print failed');
    }
    logger.info('printer', 'printer_done', `Print completed: ${filename}`, { filename });
    fetchGallery();
    return await r.json();
  }, [fetchGallery]);

  /* ── Save config (admin) ── */
  const saveConfig = useCallback(async (updates) => {
    const r = await fetch(`${API}/api/config`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    if (!r.ok) throw new Error('Config save failed');
    const data = await r.json();
    setConfig(data);
    configRef.current = data;
    return data;
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
    // Refresh config to get updated PIN
    await fetchConfig();
    return await r.json();
  }, [fetchConfig]);

  /* ── Recent Logs ── */
  const getRecentLogs = useCallback(async (count = 50, source = 'both') => {
    const r = await fetch(`${API}/api/logs/recent?count=${count}&source=${source}`);
    if (!r.ok) throw new Error('Failed to fetch logs');
    return await r.json();
  }, []);

  return {
    config,
    isOnline,
    gallery,
    fetchConfig,
    fetchGallery,
    printPhoto,
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
