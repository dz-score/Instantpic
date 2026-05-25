import React from 'react';
import './ProgressDots.css';

/**
 * Step indicator dots for collage mode (1 of 3, 2 of 3, etc.).
 */
export default function ProgressDots({ current, total }) {
  return (
    <div className="progress-dots">
      <span className="progress-dots__label">
        Shot {Math.min(current + 1, total)} of {total}
      </span>
      <div className="progress-dots__track">
        {Array.from({ length: total }, (_, i) => (
          <div
            key={i}
            className={[
              'progress-dots__dot',
              i < current ? 'progress-dots__dot--done' : '',
              i === current ? 'progress-dots__dot--active' : '',
            ].filter(Boolean).join(' ')}
          />
        ))}
      </div>
    </div>
  );
}
