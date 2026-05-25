import React from 'react';
import './Button.css';

/**
 * Unified button component with variant and size props.
 * All buttons meet the 56px minimum touch target.
 *
 * Variants: primary (gold gradient), ghost (outline), icon (circle)
 * Sizes:    default (72px), large (80px), small (56px)
 */
export default function Button({
  variant = 'primary',
  size = 'default',
  glow = false,
  children,
  className = '',
  ...props
}) {
  return (
    <button
      className={[
        'btn',
        `btn--${variant}`,
        `btn--${size}`,
        glow ? 'btn--glow' : '',
        className,
      ].filter(Boolean).join(' ')}
      {...props}
    >
      {children}
    </button>
  );
}
