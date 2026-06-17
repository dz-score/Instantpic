import React, { useEffect, useState } from 'react';
import './ScreenShell.css';

/**
 * Full-viewport wrapper for every screen.
 * Handles fade-in/slide-up entrance animation.
 */
export default function ScreenShell({ children, className = '', center = true }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Double rAF ensures the initial styles are painted before animating
    const raf = requestAnimationFrame(() => {
      requestAnimationFrame(() => setVisible(true));
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div
      className={[
        'screen-shell',
        visible ? 'screen-shell--visible' : '',
        center ? 'screen-shell--center' : '',
        className,
      ].filter(Boolean).join(' ')}
    >
      <div className="screen-glow" aria-hidden="true" />
      {children}
    </div>
  );
}
