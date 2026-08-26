import React, { useState, useEffect, useCallback } from 'react';
import './SystemTab.css';

/**
 * System tab — live diagnostics, emergency controls, PIN change, log viewer.
 *
 * Rule 7 escape hatch (documented): diagnostics and logs are polled over REST,
 * not pushed over SSE. Both are live backend probes with no event source —
 * printer/storage come from a CUPS query + shutil.disk_usage() on every
 * /api/diagnostics call (see backend/diagnostics.py), and logs are a tail of
 * the log files. There is no SSE channel to react to, so periodic polling is
 * the intended mechanism. It only runs while this panel is mounted (and, for
 * logs, only when the operator opts into Auto), and every interval is cleared
 * on unmount.
 */
export default function SystemTab({ getDiagnostics, emergencyAction, changePin, currentPin, getRecentLogs, cameraStatus }) {
  const [diagnostics, setDiagnostics] = useState(null);
  const [loading, setLoading] = useState({});
  const [confirmAction, setConfirmAction] = useState(null);
  const [actionResult, setActionResult] = useState(null);

  // PIN change state
  const [showPinChange, setShowPinChange] = useState(false);
  const [newPin, setNewPin] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [pinError, setPinError] = useState('');
  const [pinSuccess, setPinSuccess] = useState(false);

  // Log viewer state
  const [logs, setLogs] = useState([]);
  const [logSource, setLogSource] = useState('both');
  const [logLevel, setLogLevel] = useState('all');
  const [logLoading, setLogLoading] = useState(false);
  const [logAutoRefresh, setLogAutoRefresh] = useState(false);

  // Fetch diagnostics on mount and every 5s.
  // Rule 7 escape hatch: printer/storage health has no SSE channel (they are
  // live CUPS/disk probes), so we poll while the panel is open.
  const fetchDiag = useCallback(async () => {
    try {
      const data = await getDiagnostics();
      setDiagnostics(data);
    } catch {
      /* silent */
    }
  }, [getDiagnostics]);

  useEffect(() => {
    fetchDiag();
    const id = setInterval(fetchDiag, 5000);
    return () => clearInterval(id);
  }, [fetchDiag]);

  // Fetch logs
  const fetchLogs = useCallback(async () => {
    if (!getRecentLogs) return;
    setLogLoading(true);
    try {
      const data = await getRecentLogs(80, logSource);
      setLogs(data);
    } catch { /* silent */ }
    finally { setLogLoading(false); }
  }, [getRecentLogs, logSource]);

  // Auto-refresh logs — Rule 7 escape hatch, opt-in and off by default.
  // Logs have no event channel; when the operator enables "Auto" we poll
  // /api/logs/recent every 5s and clear the interval the moment it is turned
  // off or the tab unmounts.
  useEffect(() => {
    if (!logAutoRefresh) return;
    fetchLogs();
    const id = setInterval(fetchLogs, 5000);
    return () => clearInterval(id);
  }, [logAutoRefresh, fetchLogs]);

  // Emergency action with confirmation
  const handleEmergency = async (action) => {
    if (confirmAction !== action) {
      setConfirmAction(action);
      setActionResult(null);
      // Auto-cancel confirmation after 5 seconds
      setTimeout(() => setConfirmAction(null), 5000);
      return;
    }

    setLoading((l) => ({ ...l, [action]: true }));
    setConfirmAction(null);
    try {
      const result = await emergencyAction(action);
      setActionResult({ action, ...result });
    } catch {
      setActionResult({ action, status: 'error', detail: 'Action failed' });
    } finally {
      setLoading((l) => ({ ...l, [action]: false }));
      setTimeout(() => setActionResult(null), 4000);
    }
  };

  // PIN change
  const handlePinChange = async () => {
    setPinError('');
    if (newPin.length < 6) {
      setPinError('PIN must be exactly 6 digits');
      return;
    }
    if (newPin !== confirmPin) {
      setPinError('PINs do not match');
      return;
    }
    try {
      await changePin(currentPin, newPin);
      setPinSuccess(true);
      setNewPin('');
      setConfirmPin('');
      setTimeout(() => {
        setPinSuccess(false);
        setShowPinChange(false);
      }, 2000);
    } catch (err) {
      setPinError('Failed to change PIN');
    }
  };

  const printer = diagnostics?.printer;
  const storage = diagnostics?.storage;
  const led = diagnostics?.led;
  // The tail is what matters, not the average: a mean hides exactly the retry
  // storm that would put a dark frame in a photo (Docs/LED_UART_SWITCH.md).
  const ledCaptureP95 = led?.latency_ms?.CAPTURE?.p95;

  // restart_camera has no backend implementation (the camera reconnects
  // automatically via its backoff loop); the backend answers it with an
  // honest "unsupported", which renders in the result toast below. The
  // button stays visible so the operator learns that, instead of hunting
  // for a control that quietly doesn't exist.
  const EMERGENCY_ACTIONS = [
    { id: 'restart_booth', label: 'Restart Booth', desc: 'Restarts Chromium and the backend server' },
    { id: 'restart_camera', label: 'Restart Camera', desc: 'Reconnection is automatic — tapping reports the backend status' },
    { id: 'restart_printer', label: 'Restart Printer', desc: 'Restarts the CUPS print service' },
    { id: 'clear_queue', label: 'Clear Print Queue', desc: 'Cancels all pending print jobs' },
  ];

  return (
    <div className="system-tab">
      <div className="tab-header">
        <h2 className="tab-header__title">System</h2>
        <p className="tab-header__subtitle">Monitor health and manage the booth</p>
      </div>

      {/* ═══ Live Diagnostics ═══ */}
      <section className="sys-section">
        <h3 className="sys-section__title">Live Diagnostics</h3>

        <div className="sys-diag-grid">
          {/* Printer */}
          <div className="sys-diag-card">
            <div className="sys-diag-card__header">
              <span className={`sys-dot ${printer?.connected ? 'sys-dot--green' : 'sys-dot--red'}`} />
              <span className="sys-diag-card__label">Printer</span>
            </div>
            <span className="sys-diag-card__value">
              {printer ? (printer.connected ? 'Connected' : 'Not Connected') : 'Checking…'}
            </span>
            {printer?.printer_name && (
              <span className="sys-diag-card__sub">{printer.printer_name}</span>
            )}
          </div>

          {/* Storage */}
          <div className="sys-diag-card">
            <div className="sys-diag-card__header">
              <span className={`sys-dot ${
                storage ? (storage.percentage_used < 80 ? 'sys-dot--green' : storage.percentage_used < 95 ? 'sys-dot--yellow' : 'sys-dot--red') : ''
              }`} />
              <span className="sys-diag-card__label">Storage</span>
            </div>
            {storage ? (
              <>
                <span className="sys-diag-card__value">{storage.free_gb} GB free</span>
                <div className="sys-storage-bar">
                  <div
                    className="sys-storage-bar__fill"
                    style={{ width: `${Math.min(storage.percentage_used, 100)}%` }}
                  />
                </div>
                <span className="sys-diag-card__sub">
                  {storage.photo_count} photos · {storage.used_gb} / {storage.total_gb} GB
                </span>
              </>
            ) : (
              <span className="sys-diag-card__value">Checking…</span>
            )}
          </div>

          {/* Camera (frontend-only check) */}
          <div className="sys-diag-card">
            <div className="sys-diag-card__header">
              <span className={`sys-dot ${cameraStatus?.error ? 'sys-dot--red' : 'sys-dot--green'}`} />
              <span className="sys-diag-card__label">Camera</span>
            </div>
            <span className="sys-diag-card__value">
              {cameraStatus?.error ? 'Disconnected' : 'Active'}
            </span>
            <span className="sys-diag-card__sub" style={{ fontSize: cameraStatus?.error ? '0.8rem' : 'inherit', lineHeight: 1.2 }}>
              {cameraStatus?.error ? cameraStatus.error : 'Stream initialized'}
            </span>
          </div>

          {/* LED ring */}
          <div className="sys-diag-card">
            <div className="sys-diag-card__header">
              <span className={`sys-dot ${
                !led?.enabled ? '' : led.connected ? 'sys-dot--green' : 'sys-dot--red'
              }`} />
              <span className="sys-diag-card__label">LED Ring</span>
            </div>
            <span className="sys-diag-card__value">
              {!led ? 'Checking…'
                : !led.enabled ? 'Disabled'
                : led.connected ? (ledCaptureP95 != null ? `${ledCaptureP95} ms p95` : 'Connected')
                : 'Unreachable'}
            </span>
            <span className="sys-diag-card__sub">
              {!led?.enabled ? 'Configure in the LEDs tab'
                : led.connected ? (led.description || '')
                : (led.last_error || 'No reply from the node')}
            </span>
          </div>
        </div>
      </section>

      {/* ═══ Emergency Controls ═══ */}
      <section className="sys-section">
        <h3 className="sys-section__title">Emergency Controls</h3>

        <div className="sys-emergency-grid">
          {EMERGENCY_ACTIONS.map((act) => (
            <button
              key={act.id}
              className={`sys-emergency-btn ${confirmAction === act.id ? 'sys-emergency-btn--confirm' : ''}`}
              onClick={() => handleEmergency(act.id)}
              disabled={loading[act.id]}
            >
              <span className="sys-emergency-btn__label">
                {loading[act.id]
                  ? 'Running…'
                  : confirmAction === act.id
                  ? 'Tap again to confirm'
                  : act.label}
              </span>
              <span className="sys-emergency-btn__desc">{act.desc}</span>
            </button>
          ))}
        </div>

        {actionResult && (
          <div className={`sys-action-result sys-action-result--${actionResult.status === 'success' || actionResult.status === 'mock' ? 'ok' : 'err'}`}>
            {actionResult.detail}
          </div>
        )}
      </section>

      {/* ═══ Staff Lock ═══ */}
      <section className="sys-section">
        <h3 className="sys-section__title">Staff Lock</h3>

        {!showPinChange ? (
          <div className="sys-pin-row">
            <span className="sys-pin-current">Current PIN: ••••••</span>
            <button className="sys-pin-change-btn" onClick={() => setShowPinChange(true)}>
              Change PIN
            </button>
          </div>
        ) : (
          <div className="sys-pin-form">
            <label className="admin-field">
              <span className="admin-field__label">New PIN</span>
              <input
                className="admin-field__input sys-pin-input"
                type="password"
                value={newPin}
                onChange={(e) => setNewPin(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="••••••"
                inputMode="numeric"
              />
            </label>
            <label className="admin-field">
              <span className="admin-field__label">Confirm PIN</span>
              <input
                className="admin-field__input sys-pin-input"
                type="password"
                value={confirmPin}
                onChange={(e) => setConfirmPin(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="••••••"
                inputMode="numeric"
              />
            </label>
            {pinError && <p className="sys-pin-error">{pinError}</p>}
            {pinSuccess && <p className="sys-pin-success">PIN changed successfully!</p>}
            <div className="sys-pin-actions">
              <button className="sys-pin-cancel" onClick={() => { setShowPinChange(false); setPinError(''); }}>
                Cancel
              </button>
              <button className="sys-pin-save" onClick={handlePinChange}>
                Save New PIN
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ═══ Recent Logs ═══ */}
      <section className="sys-section">
        <h3 className="sys-section__title">Recent Logs</h3>

        <div className="sys-log-controls">
          <div className="sys-log-filters">
            <select
              className="sys-log-select"
              value={logSource}
              onChange={(e) => setLogSource(e.target.value)}
            >
              <option value="both">All Sources</option>
              <option value="backend">Backend</option>
              <option value="frontend">Frontend</option>
            </select>
            <select
              className="sys-log-select"
              value={logLevel}
              onChange={(e) => setLogLevel(e.target.value)}
            >
              <option value="all">All Levels</option>
              <option value="ERROR">Errors</option>
              <option value="WARN">Warnings</option>
              <option value="INFO">Info</option>
            </select>
          </div>
          <div className="sys-log-actions">
            <label className="sys-log-auto">
              <input
                type="checkbox"
                checked={logAutoRefresh}
                onChange={(e) => setLogAutoRefresh(e.target.checked)}
              />
              Auto
            </label>
            <button
              className="sys-log-refresh"
              onClick={fetchLogs}
              disabled={logLoading}
            >
              {logLoading ? '…' : '↻ Refresh'}
            </button>
          </div>
        </div>

        <div className="sys-log-table-wrap">
          {logs.length === 0 ? (
            <p className="sys-log-empty">
              {logLoading ? 'Loading…' : 'No logs yet. Tap Refresh to load.'}
            </p>
          ) : (
            <table className="sys-log-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Lvl</th>
                  <th>Src</th>
                  <th>Module</th>
                  <th>Event</th>
                  <th>Message</th>
                </tr>
              </thead>
              <tbody>
                {logs
                  .filter((l) => logLevel === 'all' || l.level === logLevel)
                  .map((entry, i) => (
                    <tr key={i} className={`sys-log-row sys-log-row--${(entry.level || '').toLowerCase()}`}>
                      <td className="sys-log-ts">
                        {entry.ts ? new Date(entry.ts).toLocaleTimeString() : '—'}
                      </td>
                      <td className="sys-log-level">{entry.level}</td>
                      <td className="sys-log-src">{entry.source === 'backend' ? 'BE' : 'FE'}</td>
                      <td className="sys-log-mod">{entry.module}</td>
                      <td className="sys-log-evt">{entry.event}</td>
                      <td className="sys-log-msg">{entry.msg}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
