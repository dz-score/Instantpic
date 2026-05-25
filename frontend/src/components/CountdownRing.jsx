import React from 'react';
import './CountdownRing.css';

const PROMPTS = {
  3: 'Get ready!',
  2: 'Looking great!',
  1: 'Say cheese!',
};

/**
 * Animated SVG countdown ring with contextual text prompts.
 * Displays a shrinking arc and spring-animated number.
 */
export default function CountdownRing({ count, total = 3 }) {
  const radius = 76;
  const circumference = 2 * Math.PI * radius;
  const progress = ((total - count + 1) / total) * circumference;

  return (
    <div className="countdown-ring">
      <svg className="countdown-ring__svg" viewBox="0 0 200 200">
        {/* Background track */}
        <circle
          cx="100" cy="100" r={radius}
          fill="none"
          stroke="rgba(255,255,255,0.15)"
          strokeWidth="3"
        />
        {/* Animated arc */}
        <circle
          className="countdown-ring__arc"
          cx="100" cy="100" r={radius}
          fill="none"
          stroke="var(--gold-light)"
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference - progress}
          transform="rotate(-90 100 100)"
        />
      </svg>

      {/* Number with spring pop */}
      <div className="countdown-ring__number" key={count}>
        {count}
      </div>

      {/* Contextual prompt */}
      <div className="countdown-ring__prompt">
        {PROMPTS[count] || ''}
      </div>
    </div>
  );
}
