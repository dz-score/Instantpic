import React from 'react';
import ToggleSwitch from '../controls/ToggleSwitch';
import './EventTab.css';

/**
 * Event information tab — couple names, date, welcome & thank-you messages.
 * Large inputs, clear labels, no technical jargon.
 */
export default function EventTab({ form, onChange }) {
  const update = (key, value) => onChange({ ...form, [key]: value });

  return (
    <div className="event-tab">
      <div className="tab-header">
        <h2 className="tab-header__title">Event Details</h2>
        <p className="tab-header__subtitle">Personalize the booth for your event</p>
      </div>

      <div className="event-tab__fields">
        <label className="admin-field">
          <span className="admin-field__label">Couple Names</span>
          <input
            className="admin-field__input"
            type="text"
            value={form.couple_names || ''}
            onChange={(e) => update('couple_names', e.target.value)}
            placeholder="Sarah & Michael"
          />
          <span className="admin-field__hint">Displayed on the welcome screen and printed photos</span>
        </label>

        <label className="admin-field">
          <span className="admin-field__label">Event Date</span>
          <input
            className="admin-field__input"
            type="text"
            value={form.event_date || ''}
            onChange={(e) => update('event_date', e.target.value)}
            placeholder="June 14, 2026"
          />
          <span className="admin-field__hint">Shown below the couple names</span>
        </label>

        <ToggleSwitch
          id="names-on-photo"
          label="Print names on photo"
          checked={form.show_names_on_photo !== false}
          onChange={(v) => update('show_names_on_photo', v)}
        />
        <span className="admin-field__hint" style={{ marginTop: '-8px' }}>
          {form.show_names_on_photo !== false
            ? 'Couple names & date will appear on printed photos'
            : 'Photos will print without names or date'}
        </span>

        <div className="event-tab__divider" />

        <label className="admin-field">
          <span className="admin-field__label">WiFi Network Name</span>
          <input
            className="admin-field__input"
            type="text"
            value={form.wifi_network_name || ''}
            onChange={(e) => update('wifi_network_name', e.target.value)}
            placeholder="Our Wedding WiFi"
          />
          <span className="admin-field__hint">Shown on the print screen so guests know which WiFi to connect to</span>
        </label>

        <label className="admin-field">
          <span className="admin-field__label">Welcome Message</span>
          <input
            className="admin-field__input"
            type="text"
            value={form.welcome_message || ''}
            onChange={(e) => update('welcome_message', e.target.value)}
            placeholder="Create a Beautiful Memory"
          />
          <span className="admin-field__hint">Large heading on the idle screen — guests see this first</span>
        </label>

        <label className="admin-field">
          <span className="admin-field__label">Thank You Message</span>
          <input
            className="admin-field__input"
            type="text"
            value={form.thank_you_message || ''}
            onChange={(e) => update('thank_you_message', e.target.value)}
            placeholder="Thank you for celebrating with us!"
          />
          <span className="admin-field__hint">Shown after the photo prints successfully</span>
        </label>
      </div>
    </div>
  );
}
