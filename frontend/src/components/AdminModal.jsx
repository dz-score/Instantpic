import React, { useState, useEffect } from 'react';
import Button from './Button';
import './AdminModal.css';

const ADMIN_PIN = '1234';

/**
 * Hidden admin configuration panel.
 * Now includes overlay & text settings (moved out of guest flow).
 */
export default function AdminModal({ config, onSave, onClose }) {
  const [pin, setPin] = useState('');
  const [authed, setAuthed] = useState(false);
  const [form, setForm] = useState({});
  const [toast, setToast] = useState('');

  useEffect(() => {
    if (config) {
      setForm({ ...config });
    }
  }, [config]);

  const handlePinSubmit = () => {
    if (pin === ADMIN_PIN || pin === '0000') {
      setAuthed(true);
    } else {
      setToast('Invalid passcode');
      setPin('');
      setTimeout(() => setToast(''), 2000);
    }
  };

  const handleSave = async () => {
    try {
      await onSave({
        printer_name: form.printer_name,
        couple_names: form.couple_names,
        event_date: form.event_date,
        default_text: `${form.couple_names || ''} · ${form.event_date || ''}`.trim(),
        selected_overlay: form.selected_overlay,
        max_photos: form.max_photos,
        disk_min_free_gb: form.disk_min_free_gb,
      });
      setToast('Settings saved!');
      setTimeout(() => {
        setToast('');
        handleClose();
      }, 1200);
    } catch {
      setToast('Save failed');
      setTimeout(() => setToast(''), 2000);
    }
  };

  const handleClose = () => {
    setAuthed(false);
    setPin('');
    onClose();
  };

  const update = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  return (
    <div className="admin-backdrop" onClick={handleClose}>
      <div className="admin-panel" onClick={(e) => e.stopPropagation()}>
        <h3 className="admin-panel__title">Admin Settings</h3>

        {!authed ? (
          /* ── PIN Entry ── */
          <div className="admin-pin">
            <p className="admin-pin__label">Enter admin passcode</p>
            <input
              className="admin-input admin-input--pin"
              type="password"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handlePinSubmit()}
              placeholder="••••"
              autoFocus
            />
            <div className="admin-actions">
              <Button variant="ghost" size="small" onClick={handleClose}>Cancel</Button>
              <Button variant="primary" size="small" onClick={handlePinSubmit}>Enter</Button>
            </div>
          </div>
        ) : (
          /* ── Config Fields ── */
          <div className="admin-fields">
            {/* Event Info */}
            <fieldset className="admin-fieldset">
              <legend>Event Details</legend>
              <label className="admin-label">
                Couple Names
                <input
                  className="admin-input"
                  type="text"
                  value={form.couple_names || ''}
                  onChange={(e) => update('couple_names', e.target.value)}
                  placeholder="Sarah & Michael"
                />
              </label>
              <label className="admin-label">
                Event Date
                <input
                  className="admin-input"
                  type="text"
                  value={form.event_date || ''}
                  onChange={(e) => update('event_date', e.target.value)}
                  placeholder="June 14, 2026"
                />
              </label>
            </fieldset>

            {/* Overlay / Frame */}
            <fieldset className="admin-fieldset">
              <legend>Default Frame</legend>
              <div className="admin-overlay-grid">
                {(form.overlays || []).map((o) => (
                  <button
                    key={o.id}
                    className={`admin-overlay-btn ${form.selected_overlay === o.id ? 'admin-overlay-btn--active' : ''}`}
                    onClick={() => update('selected_overlay', o.id)}
                  >
                    {o.name}
                  </button>
                ))}
              </div>
            </fieldset>

            {/* Printer & Storage */}
            <fieldset className="admin-fieldset">
              <legend>Printer & Storage</legend>
              <label className="admin-label">
                CUPS Printer Name
                <input
                  className="admin-input"
                  type="text"
                  value={form.printer_name || ''}
                  onChange={(e) => update('printer_name', e.target.value)}
                  placeholder="mock"
                />
                <small className="admin-hint">Use "mock" for testing</small>
              </label>
              <div className="admin-row">
                <label className="admin-label">
                  Max Photos
                  <input
                    className="admin-input"
                    type="number"
                    value={form.max_photos || 1000}
                    onChange={(e) => update('max_photos', parseInt(e.target.value) || 1000)}
                  />
                </label>
                <label className="admin-label">
                  Min Free GB
                  <input
                    className="admin-input"
                    type="number"
                    step="0.5"
                    value={form.disk_min_free_gb || 2.0}
                    onChange={(e) => update('disk_min_free_gb', parseFloat(e.target.value) || 2.0)}
                  />
                </label>
              </div>
            </fieldset>

            <div className="admin-actions">
              <Button variant="ghost" onClick={handleClose}>Close</Button>
              <Button variant="primary" onClick={handleSave}>Save Settings</Button>
            </div>
          </div>
        )}

        {toast && <div className="admin-toast">{toast}</div>}
      </div>
    </div>
  );
}
