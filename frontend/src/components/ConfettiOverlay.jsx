import React, { useMemo } from 'react';
import './ConfettiOverlay.css';

const PARTICLE_COUNT = 28;
const COLORS = ['#C9A96E', '#DFC9A0', '#D4A0A0', '#E8DCCC', '#8B9E8B', '#B07D7D'];

/**
 * Subtle gold/blush confetti that falls gently on the reveal screen.
 * Pure CSS animation — no canvas, no JavaScript animation loop.
 */
export default function ConfettiOverlay() {
  const particles = useMemo(
    () =>
      Array.from({ length: PARTICLE_COUNT }, (_, i) => ({
        id: i,
        color: COLORS[i % COLORS.length],
        left: Math.random() * 100,
        delay: Math.random() * 2,
        duration: 3.5 + Math.random() * 2.5,
        width: 4 + Math.random() * 6,
        height: 6 + Math.random() * 10,
        rotate: Math.random() * 360,
      })),
    []
  );

  return (
    <div className="confetti-overlay" aria-hidden="true">
      {particles.map((p) => (
        <div
          key={p.id}
          className="confetti-particle"
          style={{
            left: `${p.left}%`,
            animationDelay: `${p.delay}s`,
            animationDuration: `${p.duration}s`,
            width: `${p.width}px`,
            height: `${p.height}px`,
            backgroundColor: p.color,
            transform: `rotate(${p.rotate}deg)`,
          }}
        />
      ))}
    </div>
  );
}
