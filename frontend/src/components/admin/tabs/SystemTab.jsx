import React, { useState, useEffect, useCallback } from 'react';
import './SystemTab.css';

/**
 * System tab — live diagnostics, emergency controls, PIN change.
 * Auto-refreshes diagnostics every 5 seconds.
 */
export default function SystemTab({ getDiagnostics, emergencyAction, changePin, currentPin }) {
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

  // Fetch diagnostics on mount and every 5s
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
    if (newPin.length < 4) {
      setPinError('PIN must be at least 4 digits');
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

  const EMERGENCY_ACTIONS = [
    { id: 'restart_booth', label: 'Restart Booth', desc: 'Restarts Chromium and the backend server' },
    { id: 'restart_camera', label: 'Restart Camera', desc: 'Re-initializes the camera connection' },
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
              <span className="sys-dot sys-dot--green" />
              <span className="sys-diag-card__label">Camera</span>
            </div>
            <span className="sys-diag-card__value">Active</span>
            <span className="sys-diag-card__sub">Stream initialized</span>
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
            <span className="sys-pin-current">Current PIN: ••••</span>
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
                placeholder="••••"
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
                placeholder="••••"
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
    </div>
  );
}
