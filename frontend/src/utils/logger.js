/**
 * Structured JSONL logger for the photo booth frontend.
 *
 * - Buffers log entries in memory
 * - Flushes to backend /api/logs every 10 seconds (or immediately on errors)
 * - Catches uncaught JS errors and unhandled promise rejections
 * - Session ID tracks one guest's full journey
 *
 * Usage:
 *   import { logger } from '../utils/logger';
 *   logger.info('ui', 'ui_screen_change', 'Navigated to COUNTDOWN', { from: 'ATTRACT' });
 *   logger.error('camera', 'camera_init_fail', 'getUserMedia failed', { error: err.message });
 */

const FLUSH_INTERVAL_MS = 10_000; // 10 seconds
const API_URL = '/api/logs';

// ── Session ID ──
let _sessionId = null;

function generateSessionId() {
  const chars = 'abcdef0123456789';
  let id = 's_';
  for (let i = 0; i < 6; i++) id += chars[Math.floor(Math.random() * chars.length)];
  return id;
}

export function startSession() {
  _sessionId = generateSessionId();
  logger.info('session', 'session_start', 'Guest tapped start');
  return _sessionId;
}

export function endSession(reason = 'completed') {
  logger.info('session', 'session_end', `Session ended: ${reason}`, { reason });
  _sessionId = null;
}

export function getSessionId() {
  return _sessionId;
}

// ── Buffer ──
let _buffer = [];

function createEntry(level, module, event, msg, data = null, dur = null) {
  return JSON.stringify({
    ts: new Date().toISOString(),
    level,
    source: 'frontend',
    module,
    event,
    msg,
    sid: _sessionId,
    dur,
    data,
  });
}

// ── Flush to backend ──
let _flushTimer = null;
let _flushing = false;

async function flush() {
  if (_flushing || _buffer.length === 0) return;
  _flushing = true;

  const batch = _buffer.splice(0); // take all, clear buffer

  try {
    await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lines: batch }),
    });
  } catch {
    // If backend is down, put lines back (capped to prevent memory leak)
    _buffer.unshift(...batch);
    if (_buffer.length > 500) _buffer.length = 500;
  } finally {
    _flushing = false;
  }
}

function scheduleFlush() {
  if (_flushTimer) return;
  _flushTimer = setInterval(flush, FLUSH_INTERVAL_MS);
}

// ── Public Logger API ──
export const logger = {
  debug(module, event, msg, data) {
    _buffer.push(createEntry('DEBUG', module, event, msg, data));
    scheduleFlush();
  },

  info(module, event, msg, data, dur) {
    _buffer.push(createEntry('INFO', module, event, msg, data, dur));
    scheduleFlush();
  },

  warn(module, event, msg, data) {
    _buffer.push(createEntry('WARN', module, event, msg, data));
    scheduleFlush();
  },

  error(module, event, msg, data) {
    _buffer.push(createEntry('ERROR', module, event, msg, data));
    // Flush immediately on errors
    flush();
  },

  /** Force a flush (e.g. before page unload) */
  flush,
};

// ── Global Error Handlers ──
window.addEventListener('error', (e) => {
  logger.error('system', 'js_uncaught_error', e.message || 'Uncaught error', {
    filename: e.filename,
    lineno: e.lineno,
    colno: e.colno,
  });
});

window.addEventListener('unhandledrejection', (e) => {
  const reason = e.reason instanceof Error ? e.reason.message : String(e.reason);
  logger.error('system', 'js_unhandled_rejection', reason);
});

// Flush on page unload
window.addEventListener('beforeunload', () => {
  flush();
});

// Start the flush timer immediately
scheduleFlush();
