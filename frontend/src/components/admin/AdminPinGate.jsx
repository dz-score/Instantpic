import React, { useState } from 'react';
import './AdminPinGate.css';

/**
 * Full-screen PIN entry gate for admin access.
 * Large numpad optimized for touch — no keyboard needed.
 */
export default function AdminPinGate({ correctPin, onSuccess, onCancel }) {
  const [pin, setPin] = useState('');
  const [error, setError] = useState(false);

  const handleDigit = (d) => {
    if (pin.length >= 6) return;
    const next = pin + d;
    setPin(next);
    setError(false);
  };

  const handleDelete = () => {
    setPin((p) => p.slice(0, -1));
    setError(false);
  };

  const handleSubmit = () => {
    if (pin === correctPin) {
      onSuccess();
    } else {
      setError(true);
      setPin('');
      setTimeout(() => setError(false), 1500);
    }
  };

  const digits = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', ''];

  return (
    <div className="pin-gate">
      <div className="pin-gate__card">
        <h2 className="pin-gate__title">Admin Access</h2>
        <p className="pin-gate__subtitle">Enter your passcode</p>

        {/* PIN dots */}
        <div className="pin-gate__dots">
          {Array.from({ length: 4 }, (_, i) => (
            <span
              key={i}
              className={[
                'pin-gate__dot',
                i < pin.length ? 'pin-gate__dot--filled' : '',
                error ? 'pin-gate__dot--error' : '',
              ].filter(Boolean).join(' ')}
            />
          ))}
        </div>

        {error && <p className="pin-gate__error">Incorrect passcode</p>}

        {/* Numpad */}
        <div className="pin-gate__numpad">
          {digits.map((d, i) => {
            if (d === '' && i === 9) {
              return (
                <button
                  key="cancel"
                  className="pin-gate__key pin-gate__key--action"
                  onClick={onCancel}
                >
                  Cancel
                </button>
              );
            }
            if (d === '' && i === 11) {
              return (
                <button
                  key="enter"
                  className="pin-gate__key pin-gate__key--action pin-gate__key--submit"
                  onClick={handleSubmit}
                  disabled={pin.length < 4}
                >
                  Enter
                </button>
              );
            }
            return (
              <button
                key={d}
                className="pin-gate__key"
                onClick={() => handleDigit(d)}
              >
                {d}
              </button>
            );
          })}
        </div>

        {/* Delete / backspace */}
        <button
          className="pin-gate__backspace"
          onClick={handleDelete}
          disabled={pin.length === 0}
        >
          ← Delete
        </button>
      </div>
    </div>
  );
}
