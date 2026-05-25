import React, { useEffect, useState } from 'react';
import './Toast.css';

/**
 * Non-blocking notification toast. Replaces native alert().
 * Auto-dismisses after `duration` ms.
 */
export default function Toast({ message, type = 'info', duration = 4000, onDismiss }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Enter
    const enterRaf = requestAnimationFrame(() => setVisible(true));

    // Auto-dismiss
    const timer = setTimeout(() => {
      setVisible(false);
      setTimeout(() => onDismiss?.(), 350);
    }, duration);

    return () => {
      cancelAnimationFrame(enterRaf);
      clearTimeout(timer);
    };
  }, [duration, onDismiss]);

  return (
    <div className={`toast toast--${type} ${visible ? 'toast--visible' : ''}`}>
      <span className="toast__icon">
        {type === 'success' ? '✓' : type === 'error' ? '!' : '●'}
      </span>
      <span className="toast__message">{message}</span>
    </div>
  );
}
