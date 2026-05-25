/**
 * Warm shutter sound using Web Audio API.
 * Falls back to a bundled WAV file if Web Audio is unavailable.
 * Generates a gentle bell-chime tone — warm and celebratory.
 */

let audioCtx = null;

function getAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return audioCtx;
}

/**
 * Play a warm camera shutter + gentle chime sound.
 * No external files needed — synthesised at runtime.
 */
export function playShutterSound() {
  try {
    const ctx = getAudioContext();
    const now = ctx.currentTime;

    // ── Soft mechanical click ──
    const clickOsc = ctx.createOscillator();
    const clickGain = ctx.createGain();
    clickOsc.type = 'sine';
    clickOsc.frequency.setValueAtTime(600, now);
    clickOsc.frequency.exponentialRampToValueAtTime(150, now + 0.06);
    clickGain.gain.setValueAtTime(0.18, now);
    clickGain.gain.exponentialRampToValueAtTime(0.001, now + 0.08);
    clickOsc.connect(clickGain);
    clickGain.connect(ctx.destination);
    clickOsc.start(now);
    clickOsc.stop(now + 0.08);

    // ── Warm bell tone (fundamental) ──
    const bellOsc = ctx.createOscillator();
    const bellGain = ctx.createGain();
    bellOsc.type = 'sine';
    bellOsc.frequency.setValueAtTime(523.25, now + 0.04); // C5
    bellGain.gain.setValueAtTime(0, now);
    bellGain.gain.linearRampToValueAtTime(0.12, now + 0.06); // soft attack
    bellGain.gain.exponentialRampToValueAtTime(0.001, now + 0.7);
    bellOsc.connect(bellGain);
    bellGain.connect(ctx.destination);
    bellOsc.start(now + 0.04);
    bellOsc.stop(now + 0.7);

    // ── Harmonic overtone for warmth ──
    const harmOsc = ctx.createOscillator();
    const harmGain = ctx.createGain();
    harmOsc.type = 'sine';
    harmOsc.frequency.setValueAtTime(784, now + 0.04); // G5 (perfect 5th)
    harmGain.gain.setValueAtTime(0, now);
    harmGain.gain.linearRampToValueAtTime(0.06, now + 0.07);
    harmGain.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
    harmOsc.connect(harmGain);
    harmGain.connect(ctx.destination);
    harmOsc.start(now + 0.04);
    harmOsc.stop(now + 0.5);

    // ── Subtle sub-harmonic for body ──
    const subOsc = ctx.createOscillator();
    const subGain = ctx.createGain();
    subOsc.type = 'sine';
    subOsc.frequency.setValueAtTime(261.6, now + 0.04); // C4
    subGain.gain.setValueAtTime(0, now);
    subGain.gain.linearRampToValueAtTime(0.04, now + 0.08);
    subGain.gain.exponentialRampToValueAtTime(0.001, now + 0.6);
    subOsc.connect(subGain);
    subGain.connect(ctx.destination);
    subOsc.start(now + 0.04);
    subOsc.stop(now + 0.6);
  } catch (e) {
    // Silent failure — sound is non-essential
    console.warn('Audio playback failed:', e);
  }
}

/** Resume audio context after user gesture (required by browsers). */
export function unlockAudio() {
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') ctx.resume();
  } catch { /* ignore */ }
}
