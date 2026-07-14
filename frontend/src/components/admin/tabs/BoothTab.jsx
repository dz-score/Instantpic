import React from 'react';
import ToggleSwitch from '../controls/ToggleSwitch';
import './BoothTab.css';

// 10 is the ceiling: the countdown ring video is 10s long (CountdownScreen.jsx).
const COUNTDOWN_OPTIONS = [3, 5, 10];
const SESSION_TIMEOUT_OPTIONS = [60, 90, 120, 180, 300];

/**
 * Booth settings tab — countdown, flash, session rules.
 * All controls are visual (toggle buttons, large switches) — no typing needed.
 */
export default function BoothTab({ form, onChange }) {
  const update = (key, value) => onChange({ ...form, [key]: value });

  const formatTimeout = (s) => {
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return rem ? `${m}m ${rem}s` : `${m} min`;
  };

  return (
    <div className="booth-tab">
      <div className="tab-header">
        <h2 className="tab-header__title">Booth Settings</h2>
        <p className="tab-header__subtitle">Control the photo-taking experience</p>
      </div>

      {/* Countdown Duration */}
      <div className="booth-section">
        <span className="booth-section__label">Countdown Timer</span>
        <span className="booth-section__hint">How long guests have to get ready</span>
        <div className="booth-toggle-group">
          {COUNTDOWN_OPTIONS.map((val) => (
            <button
              key={val}
              className={`booth-toggle-btn ${form.countdown_duration === val ? 'booth-toggle-btn--active' : ''}`}
              onClick={() => update('countdown_duration', val)}
            >
              {val}s
            </button>
          ))}
        </div>
      </div>

      {/* Flash Effect */}
      <div className="booth-section">
        <ToggleSwitch
          id="flash-toggle"
          label="Camera Flash Effect"
          checked={form.flash_enabled !== false}
          onChange={(v) => update('flash_enabled', v)}
        />
        <span className="booth-section__hint">
          {form.flash_enabled !== false
            ? 'Warm champagne flash on capture'
            : 'No flash — silent capture'}
        </span>
      </div>

      {/* Max Photos per Session */}
      <div className="booth-section">
        <span className="booth-section__label">Photos per Session</span>
        <span className="booth-section__hint">Maximum retakes allowed per group</span>
        <div className="booth-stepper">
          <button
            className="booth-stepper__btn"
            onClick={() => update('max_photos_per_session', Math.max(1, (form.max_photos_per_session || 5) - 1))}
            disabled={(form.max_photos_per_session || 5) <= 1}
          >
            −
          </button>
          <span className="booth-stepper__value">{form.max_photos_per_session || 5}</span>
          <button
            className="booth-stepper__btn"
            onClick={() => update('max_photos_per_session', Math.min(5, (form.max_photos_per_session || 3) + 1))}
            disabled={(form.max_photos_per_session || 3) >= 5}
          >
            +
          </button>
        </div>
      </div>

      {/* Session Timeout */}
      <div className="booth-section">
        <span className="booth-section__label">Idle Timeout</span>
        <span className="booth-section__hint">Auto-return to welcome screen after inactivity</span>
        <div className="booth-toggle-group">
          {SESSION_TIMEOUT_OPTIONS.map((val) => (
            <button
              key={val}
              className={`booth-toggle-btn ${form.session_timeout === val ? 'booth-toggle-btn--active' : ''}`}
              onClick={() => update('session_timeout', val)}
            >
              {formatTimeout(val)}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
