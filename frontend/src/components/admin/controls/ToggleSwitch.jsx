import React from 'react';
import './ToggleSwitch.css';

/**
 * Large, touch-friendly on/off toggle.
 * 56px tall — meets kiosk minimum touch target.
 */
export default function ToggleSwitch({ checked, onChange, label, id }) {
  return (
    <div className="toggle-row">
      {label && <span className="toggle-label">{label}</span>}
      <button
        id={id}
        role="switch"
        aria-checked={checked}
        className={`toggle-switch ${checked ? 'toggle-switch--on' : ''}`}
        onClick={() => onChange(!checked)}
        type="button"
      >
        <span className="toggle-switch__thumb" />
      </button>
    </div>
  );
}
