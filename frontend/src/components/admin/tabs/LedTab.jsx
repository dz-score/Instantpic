import React, { useState, useEffect, useCallback } from 'react';
import ToggleSwitch from '../controls/ToggleSwitch';
import './LedTab.css';

/**
 * LED tab — configure the ring, and drive the strip directly to inspect it.
 *
 * The colour buttons are a bench instrument, not a booth feature: each lights
 * one physical die (R, G, B, or the separate white die) flat across all 60
 * pixels at full scale, which is the only way to find a pixel that is dead,
 * miswired, or has one channel out. PHASE cannot do this — it never lights W
 * and desaturates below sat 1.0, so it always mixes dies.
 *
 * The backend refuses every command here outside ATTRACT (see the routes in
 * backend/routers/system.py). That refusal comes back as an ordinary message
 * rather than an error.
 *
 * Health is polled over REST — the Rule 7 escape hatch SystemTab documents.
 */

const CHANNELS = [
  { id: 'red', label: 'All Red', swatch: '#ff2d2d' },
  { id: 'green', label: 'All Green', swatch: '#2dff5a' },
  { id: 'blue', label: 'All Blue', swatch: '#2d6bff' },
  { id: 'white', label: 'All White', swatch: '#fffaf0' },
];

export default function LedTab({ getDiagnostics, testLed, testLedChannel, ledConfig, onSaveLed }) {
  const [health, setHealth] = useState(null);
  const [host, setHost] = useState('');
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    setHost(ledConfig?.http?.host || '');
  }, [ledConfig?.http?.host]);

  const fetchHealth = useCallback(async () => {
    try {
      const data = await getDiagnostics();
      setHealth(data.led || null);
    } catch {
      /* silent — the next tick retries */
    }
  }, [getDiagnostics]);

  useEffect(() => {
    fetchHealth();
    const id = setInterval(fetchHealth, 5000);
    return () => clearInterval(id);
  }, [fetchHealth]);

  const saveLed = useCallback(async (patch) => {
    setSaving(true);
    setResult(null);
    try {
      await onSaveLed(patch);
      await fetchHealth();
    } catch {
      setResult({ ok: false, detail: 'Could not save' });
    } finally {
      setSaving(false);
    }
  }, [onSaveLed, fetchHealth]);

  const run = useCallback(async (label, fn) => {
    setBusy(label);
    setResult(null);
    try {
      setResult(await fn());
    } catch {
      setResult({ ok: false, detail: 'Command failed' });
    } finally {
      setBusy(null);
    }
  }, []);

  const enabled = !!ledConfig?.enabled;
  const capture = health?.latency_ms?.CAPTURE;
  const locked = saving || !!busy || !enabled;

  return (
    <div className="led-tab">
      <div className="tab-header">
        <h2 className="tab-header__title">LED Ring</h2>
        <p className="tab-header__subtitle">Configure the node and inspect the strip</p>
      </div>

      {/* ═══ Status ═══ */}
      <section className="led-section">
        <h3 className="led-section__title">Status</h3>

        <div className="led-status">
          <span className={`sys-dot ${
            !health?.enabled ? '' : health.connected ? 'sys-dot--green' : 'sys-dot--red'
          }`} />
          <div className="led-status__text">
            <span className="led-status__value">
              {!health ? 'Checking…'
                : !health.enabled ? 'Disabled'
                : health.connected ? 'Connected'
                : 'Unreachable'}
            </span>
            <span className="led-status__sub">
              {!health?.enabled ? 'No ring configured'
                : health.connected ? (health.description || '')
                : (health.last_error || 'No reply from the node')}
            </span>
          </div>
          {/* p95, not the mean — Docs/LED_UART_SWITCH.md. */}
          {capture && (
            <div className="led-status__latency">
              <span className="led-status__latency-value">{capture.p95} ms</span>
              <span className="led-status__latency-label">capture p95</span>
            </div>
          )}
        </div>

        {health?.fault != null && (
          <div className="sys-action-result sys-action-result--err">
            Ring is showing fault code {health.fault} — the booth is reporting a
            problem, most likely the camera. The strip test below still works.
          </div>
        )}
      </section>

      {/* ═══ Connection ═══ */}
      <section className="led-section">
        <h3 className="led-section__title">Connection</h3>

        <ToggleSwitch
          id="led-enabled"
          label="Ring enabled"
          checked={enabled}
          onChange={(v) => saveLed({ enabled: v })}
        />

        <div className="led-host-row">
          <label className="admin-field led-host-field">
            <span className="admin-field__label">Node address</span>
            <input
              className="admin-field__input"
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              onBlur={() => {
                if (host !== (ledConfig?.http?.host || '')) saveLed({ http: { host } });
              }}
              placeholder="192.168.4.50"
              inputMode="decimal"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
            />
          </label>
          <button className="led-btn" onClick={() => run('ping', testLed)} disabled={locked}>
            {busy === 'ping' ? 'Pinging…' : 'Ping Node'}
          </button>
        </div>

        <p className="led-hint">
          Host or IP only — no http:// and no port. Changes apply immediately; no restart.
        </p>
      </section>

      {/* ═══ Strip test ═══ */}
      <section className="led-section">
        <h3 className="led-section__title">Strip Test</h3>
        <p className="led-hint led-hint--lead">
          Lights one die across every pixel at full brightness, so a dead or miswired
          one shows up. Only available from the idle screen; the node returns to its
          idle pattern on its own after two minutes.
        </p>

        <div className="led-swatch-grid">
          {CHANNELS.map((ch) => (
            <button
              key={ch.id}
              className="led-swatch"
              style={{ '--swatch': ch.swatch }}
              onClick={() => run(ch.id, () => testLedChannel(ch.id))}
              disabled={locked}
            >
              <span className="led-swatch__chip" aria-hidden="true" />
              <span className="led-swatch__label">
                {busy === ch.id ? 'Sending…' : ch.label}
              </span>
            </button>
          ))}
        </div>

        <div className="led-test-actions">
          <button
            className="led-btn"
            onClick={() => run('all', () => testLedChannel('all'))}
            disabled={locked}
          >
            {busy === 'all' ? 'Sending…' : 'All Channels'}
          </button>
          <button
            className="led-btn led-btn--primary"
            onClick={() => run('off', () => testLedChannel('off'))}
            disabled={locked}
          >
            {busy === 'off' ? 'Stopping…' : 'Back to Idle'}
          </button>
        </div>

        {result && (
          <div className={`sys-action-result sys-action-result--${result.ok ? 'ok' : 'err'}`}>
            {result.ok
              ? (result.reply === 'PONG'
                ? `Node answered PONG in ${result.elapsed_ms} ms`
                : result.channel === 'off'
                  ? 'Ring back on the idle pattern'
                  : `Ring lit: ${result.channel}`)
              : (result.detail || 'No reply from the node')}
          </div>
        )}
      </section>
    </div>
  );
}
