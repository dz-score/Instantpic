/**
 * Warm shutter sound using Web Audio API.
 * Falls back to a bundled WAV file if Web Audio is unavailable.
 * Generates a gentle bell-chime tone — warm and celebratory.
 *
 * The AudioContext is suspended whenever no sound is playing. A running
 * context holds the system audio output open even while silent, which keeps
 * the TV/speaker amplifier powered — on this booth's hardware that produced
 * an audible ~35s whine after every tap/shutter chime (until the amp's own
 * silence timeout). Suspending releases the device so the amp sleeps within
 * seconds instead.
 */

let audioCtx = null;
let suspendTimer = null;

// Longest tone ends at +0.7s; leave margin so we never clip a tail.
const SUSPEND_AFTER_MS = 1500;

function getAudioContext() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
  return audioCtx;
}

function scheduleSuspend() {
  clearTimeout(suspendTimer);
  suspendTimer = setTimeout(() => {
    if (audioCtx && audioCtx.state === 'running') {
      audioCtx.suspend().catch(() => { /* ignore */ });
    }
  }, SUSPEND_AFTER_MS);
}

/**
 * Play a warm camera shutter + gentle chime sound.
 * No external files needed — synthesised at runtime.
 */
export function playShutterSound() {
  try {
    const ctx = getAudioContext();
    clearTimeout(suspendTimer);

    const play = () => {
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

      scheduleSuspend();
    };

    // The context is usually suspended between sounds (see header comment);
    // resume() is allowed without a fresh gesture once the page has had one.
    if (ctx.state === 'suspended') {
      ctx.resume().then(play).catch(() => { /* sound is non-essential */ });
    } else {
      play();
    }
  } catch (e) {
    // Silent failure — sound is non-essential
    console.warn('Audio playback failed:', e);
  }
}

/** Resume audio context after user gesture (required by browsers). */
export function unlockAudio() {
  try {
    const ctx = getAudioContext();
    if (ctx.state === 'suspended') {
      // Resume just long enough to earn the browser's gesture unlock, then
      // release the output device again so the amp doesn't sit whining.
      ctx.resume().then(scheduleSuspend).catch(() => { /* ignore */ });
    }
  } catch { /* ignore */ }
}
