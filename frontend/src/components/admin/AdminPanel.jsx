import React, { useState, useEffect, useCallback } from 'react';
import AdminPinGate from './AdminPinGate';
import EventTab from './tabs/EventTab';
import BoothTab from './tabs/BoothTab';
import SystemTab from './tabs/SystemTab';
import CameraTab from './tabs/CameraTab';
import LedTab from './tabs/LedTab';
import PrinterTab from './tabs/PrinterTab';
import './AdminPanel.css';

const TABS = [
  { id: 'event', icon: '♡', label: 'Event' },
  { id: 'booth', icon: '◎', label: 'Booth' },
  { id: 'camera', icon: '📷', label: 'Camera' },
  { id: 'leds', icon: '◍', label: 'LEDs' },
  { id: 'printer', icon: '🖨', label: 'Printer' },
  { id: 'system', icon: '⚙', label: 'System' },
];

/**
 * Full-page admin panel with sidebar tab navigation.
 * - PIN-gated entry
 * - Auto-save on change + visual indicator
 * - Large, calm, impossible to break
 *
 * Two save paths, deliberately. The form tabs (Event, Booth) collect edits and
 * commit them together on "Save Changes". The device tabs (LEDs, Printer) write
 * through immediately: whoever is using those is standing at the hardware, and
 * a change that only lands on "Save" is a change they cannot watch take effect.
 */
export default function AdminPanel({
  config,
  onSave,
  onClose,
  getDiagnostics,
  testLed,
  testLedChannel,
  testPrint,
  emergencyAction,
  changePin,
  getRecentLogs,
  getCameraConfig,
  saveCameraConfig,
  cameraStatus,
}) {
  const [authed, setAuthed] = useState(false);
  const [activeTab, setActiveTab] = useState('event');
  const [form, setForm] = useState({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [toast, setToast] = useState('');

  // Initialize form from config
  useEffect(() => {
    if (config) {
      setForm({ ...config });
    }
  }, [config]);

  const handleFormChange = useCallback((newForm) => {
    setForm(newForm);
    setDirty(true);
    setSaved(false);
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    try {
      await onSave({
        couple_names: form.couple_names,
        event_date: form.event_date,
        welcome_message: form.welcome_message,
        thank_you_message: form.thank_you_message,
        countdown_duration: form.countdown_duration,
        flash_enabled: form.flash_enabled,
        max_photos_per_session: form.max_photos_per_session,
        session_timeout: form.session_timeout,
        default_text: `${form.couple_names || ''} · ${form.event_date || ''}`.trim(),
        printer_name: form.printer_name,
        printer_options: form.printer_options,
        selected_overlay: form.selected_overlay,
        max_photos: form.max_photos,
        disk_min_free_gb: form.disk_min_free_gb,
        show_names_on_photo: form.show_names_on_photo,
        wifi_network_name: form.wifi_network_name,
      });
      setDirty(false);
      setSaved(true);
      setToast('Settings saved');
      setTimeout(() => { setToast(''); setSaved(false); }, 2500);
    } catch {
      setToast('Save failed — please try again');
      setTimeout(() => setToast(''), 3000);
    } finally {
      setSaving(false);
    }
  }, [form, onSave]);

  const handleClose = useCallback(() => {
    setAuthed(false);
    onClose();
  }, [onClose]);

  const adminPin = config?.admin_pin || '1234';

  // PIN gate
  if (!authed) {
    return (
      <AdminPinGate
        correctPin={adminPin}
        onSuccess={() => setAuthed(true)}
        onCancel={handleClose}
      />
    );
  }

  return (
    <div className="admin-panel-page">
      {/* ═══ Sidebar ═══ */}
      <aside className="admin-sidebar">
        <div className="admin-sidebar__header">
          <h2 className="admin-sidebar__title">Settings</h2>
        </div>

        <nav className="admin-sidebar__nav">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`admin-tab-btn ${activeTab === tab.id ? 'admin-tab-btn--active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <span className="admin-tab-btn__icon">{tab.icon}</span>
              <span className="admin-tab-btn__label">{tab.label}</span>
            </button>
          ))}
        </nav>

        <div className="admin-sidebar__footer">
          <button className="admin-close-btn" onClick={handleClose}>
            ← Back to Booth
          </button>
        </div>
      </aside>

      {/* ═══ Main Content ═══ */}
      <main className="admin-main">
        {/* Top bar */}
        <header className="admin-topbar">
          <div className="admin-topbar__status">
            {dirty && !saving && (
              <span className="admin-status-pill admin-status-pill--dirty">Unsaved changes</span>
            )}
            {saving && (
              <span className="admin-status-pill admin-status-pill--saving">Saving…</span>
            )}
            {saved && !dirty && (
              <span className="admin-status-pill admin-status-pill--saved">✓ Saved</span>
            )}
          </div>

          <div className="admin-topbar__actions">
            <button
              className="admin-save-btn"
              onClick={handleSave}
              disabled={!dirty || saving}
            >
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          </div>
        </header>

        {/* Tab content */}
        <div className="admin-content">
          {activeTab === 'event' && (
            <EventTab form={form} onChange={handleFormChange} />
          )}
          {activeTab === 'booth' && (
            <BoothTab form={form} onChange={handleFormChange} />
          )}
          {activeTab === 'camera' && (
            <CameraTab
              getCameraConfig={getCameraConfig}
              saveCameraConfig={saveCameraConfig}
            />
          )}
          {activeTab === 'leds' && (
            <LedTab
              getDiagnostics={getDiagnostics}
              testLed={testLed}
              testLedChannel={testLedChannel}
              ledConfig={config?.led}
              onSaveLed={(led) => onSave({ led })}
            />
          )}
          {activeTab === 'printer' && (
            <PrinterTab
              getDiagnostics={getDiagnostics}
              testPrint={testPrint}
              config={config}
              onSaveConfig={(patch) => onSave(patch)}
              onSaveMock={(patch) => onSave({ printer_mock: patch })}
            />
          )}
          {activeTab === 'system' && (
            <SystemTab
              getDiagnostics={getDiagnostics}
              emergencyAction={emergencyAction}
              changePin={changePin}
              currentPin={adminPin}
              getRecentLogs={getRecentLogs}
              cameraStatus={cameraStatus}
            />
          )}
        </div>
      </main>

      {/* Toast */}
      {toast && (
        <div className="admin-panel-toast">{toast}</div>
      )}
    </div>
  );
}
