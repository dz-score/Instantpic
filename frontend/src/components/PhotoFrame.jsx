import React from 'react';
import './PhotoFrame.css';

/**
 * Elegant wrapper for displaying a processed photo.
 * Adds warm shadow and optional rounded border.
 */
export default function PhotoFrame({ src, alt = 'Photo', size = 'default', className = '' }) {
  return (
    <div className={`photo-frame photo-frame--${size} ${className}`}>
      <img src={src} alt={alt} className="photo-frame__img" />
    </div>
  );
}
