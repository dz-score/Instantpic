import { useRef, useState, useCallback } from 'react';

/**
 * Rule 17 tap-guard for intent buttons.
 *
 * Guests double-tap and mash buttons during transitions. This latches on the
 * first tap so the wrapped intent fires exactly once, then the resulting state
 * change swaps the screen out (which remounts and re-arms the guard). The ref
 * blocks the second tap synchronously — before React re-renders — while the
 * `armed` flag lets buttons render disabled.
 *
 * This is UX polish, not the protection: the backend must treat repeated
 * requests as idempotent (see Rule 17). Never rely on this to prevent a
 * double print.
 *
 * Usage:
 *   const [guard, armed] = useTapGuard();
 *   <button onClick={guard(onPrint)} disabled={!armed}>Print</button>
 */
export default function useTapGuard() {
  const firedRef = useRef(false);
  const [armed, setArmed] = useState(true);

  const guard = useCallback((fn) => (...args) => {
    if (firedRef.current) return;
    firedRef.current = true;
    setArmed(false);
    fn(...args);
  }, []);

  return [guard, armed];
}
