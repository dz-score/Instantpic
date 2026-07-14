import React from 'react';
import { logger } from '../utils/logger';
import './PhotoFrame.css';

/**
 * Elegant wrapper for displaying a processed photo.
 * Adds warm shadow and optional rounded border.
 */
export default function PhotoFrame({ src, alt = 'Photo', size = 'default', className = '' }) {
  // A photo that 404s leaves the guest staring at a blank frame with no other
  // symptom — indistinguishable from a hang, and silent in every log. Report it.
  const handleError = () => {
    logger.error('ui', 'photo_load_fail', `Photo failed to load: ${src}`, { src });
  };

  return (
    <div className={`photo-frame photo-frame--${size} ${className}`}>
      <img src={src} alt={alt} className="photo-frame__img" onError={handleError} />
    </div>
  );
}
