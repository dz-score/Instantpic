import React, { useState, useEffect, useCallback } from 'react';
import './PrinterTab.css';

/**
 * Printer tab — configure the queue, watch the media, and prove the geometry.
 * Docs/PRINTER_NOTES.md is the background; this is the operator's surface onto
 * it, and its hardware run is what the test print is for.
 *
 * Print options are a free text field rather than pickers on purpose: which
 * `lp -o` string a given PPD wants is unknown until the printer is on the
 * bench, and the operator standing in front of it has to be able to try one
 * and print again rather than wait for a release.
 *
 * A refusal from the test print is rendered as an ordinary message, not an
 * error — see useApi.testPrint.
 *
 * Status here is a live CUPS query, so it polls (SystemTab documents the
 * Rule 7 escape hatch that allows it).
 */

const MOCK_FAULTS = [
  { id: 'none', label: 'No fault — prints normally' },
  { id: 'submit_fails_once', label: 'First submission rejected, retry accepted' },
  { id: 'offline', label: 'Printer offline — nothing accepted' },
  { id: 'out_of_media', label: 'Ribbon runs out mid-job' },
  { id: 'abort_mid_job', label: 'Jams mid-job' },
];

export default function PrinterTab({
  getDiagnostics,
  testPrint,
  config,
  onSaveConfig,
  onSaveMock,
}) {
  const [printer, setPrinter] = useState(null);
  const [name, setName] = useState('');
  const [options, setOptions] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  useEffect(() => { setName(config?.printer_name || ''); }, [config?.printer_name]);
  useEffect(() => { setOptions(config?.printer_options || ''); }, [config?.printer_options]);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getDiagnostics();
      setPrinter(data.printer || null);
    } catch {
      /* silent — the next tick retries */
    }
  }, [getDiagnostics]);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 5000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const commit = useCallback(async (patch) => {
    try {
      await onSaveConfig(patch);
      await fetchStatus();
    } catch {
      setResult({ ok: false, detail: 'Could not save' });
    }
  }, [onSaveConfig, fetchStatus]);

  const runTest = useCallback(async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await testPrint());
    } catch {
      setResult({ ok: false, detail: 'Test print failed to start' });
    } finally {
      setBusy(false);
      fetchStatus();
    }
  }, [testPrint, fetchStatus]);

  // The mock numbers are uncontrolled and commit on blur. Controlled would mean
  // a save per keystroke — each one an atomic file write and an SSE broadcast —
  // and typing "700" is not three edits. The key remounts the field if the value
  // changes from somewhere else, which is the only thing local state bought.
  const commitNumber = useCallback((value, current, key, save = onSaveMock) => {
    const n = Number(value);
    if (value !== '' && Number.isFinite(n) && n !== current) save({ [key]: n });
  }, [onSaveMock]);

  // A stopped queue reports connected-but-not-ready: the printer is there and
  // CUPS knows it, but it will refuse everything until re-enabled. That reads
  // nothing like "not connected" to an operator, so it must not look like it.
  const stopped = printer?.connected && !printer?.ready
    && /disabled|stopped/i.test(printer?.status || '');

  const isMock = printer?.driver === 'mock';
  const remaining = printer?.prints_remaining;
  const mock = config?.printer_mock || {};
  const used = printer?.prints_used ?? config?.prints_used ?? 0;
  const allowance = printer?.print_allowance ?? config?.print_allowance ?? 150;
  const spent = used >= allowance;

  return (
    <div className="printer-tab">
      <div className="tab-header">
        <h2 className="tab-header__title">Printer</h2>
        <p className="tab-header__subtitle">Configure the queue and check what it puts on paper</p>
      </div>

      {/* ═══ Status ═══ */}
      <section className="printer-section">
        <h3 className="printer-section__title">Status</h3>

        <div className="printer-status">
          <span className={`sys-dot ${
            !printer ? '' : !printer.connected ? 'sys-dot--red'
              : (!printer.ready || printer.media_low) ? 'sys-dot--yellow'
              : 'sys-dot--green'
          }`} />
          <div className="printer-status__text">
            <span className="printer-status__value">
              {!printer ? 'Checking…'
                : !printer.connected ? 'Not connected'
                : stopped ? 'Queue stopped'
                : printer.status}
            </span>
            <span className="printer-status__sub">
              {stopped
                ? 'Nothing will print until it is recovered — System → Recover Printer'
                : (printer?.error || printer?.printer_name || '')}
            </span>
          </div>

          {/* Absent is not empty — no reporting means no number, not a zero. */}
          {remaining != null && (
            <div className={`printer-status__media ${
              printer.media_low ? 'printer-status__media--low' : ''
            }`}>
              <span className="printer-status__media-value">{remaining}</span>
              <span className="printer-status__media-label">
                prints left{printer?.media_type ? ` · ${printer.media_type}` : ''}
              </span>
            </div>
          )}
        </div>

        <div className={`printer-allowance ${spent ? 'printer-allowance--spent' : ''}`}>
          <div className="printer-allowance__head">
            <span className="printer-allowance__label">Prints used tonight</span>
            <span className="printer-allowance__count">{used} / {allowance}</span>
          </div>
          <div className="printer-allowance__bar">
            <div
              className="printer-allowance__fill"
              style={{ width: `${Math.min(100, allowance ? (used / allowance) * 100 : 0)}%` }}
            />
          </div>
          {spent && (
            <p className="printer-hint">
              The allowance is spent. Sessions still run and guests still get their
              photo by QR — the print step is skipped until this is raised or reset.
            </p>
          )}
        </div>

        {printer?.media_low && (
          <div className="sys-action-result sys-action-result--err">
            {remaining > 0
              ? `Media is running low — ${remaining} prints left. Find the spare roll.`
              : 'Out of media — prints will fail until the roll is replaced.'}
          </div>
        )}

        {isMock && (
          <div className="printer-mock-banner">
            Simulated printer — nothing reaches paper. On Windows this is chosen
            whatever the queue name says.
          </div>
        )}
      </section>

      {/* ═══ Queue ═══ */}
      <section className="printer-section">
        <h3 className="printer-section__title">Queue</h3>

        <label className="admin-field">
          <span className="admin-field__label">CUPS queue name</span>
          <input
            className="admin-field__input"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => {
              if (name !== (config?.printer_name || '')) commit({ printer_name: name });
            }}
            placeholder="DS-RX1"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
        </label>

        <label className="admin-field printer-field--wide">
          <span className="admin-field__label">Print options</span>
          <input
            className="admin-field__input printer-field__mono"
            type="text"
            value={options}
            onChange={(e) => setOptions(e.target.value)}
            onBlur={() => {
              if (options !== (config?.printer_options || '')) {
                commit({ printer_options: options });
              }
            }}
            placeholder="media=w288h432 scaling=100"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
        </label>

        <label className="admin-field printer-field--wide">
          <span className="admin-field__label">Warn when prints left drops below</span>
          <input
            className="admin-field__input"
            type="number"
            min="0"
            step="1"
            key={`lowat-${config?.printer_media_low_threshold}`}
            defaultValue={config?.printer_media_low_threshold ?? 25}
            onBlur={(e) => commitNumber(
              e.target.value,
              config?.printer_media_low_threshold,
              'printer_media_low_threshold',
              onSaveConfig,
            )}
          />
        </label>

        <div className="printer-allowance-row">
          <label className="admin-field printer-allowance-field">
            <span className="admin-field__label">Prints allowed this event</span>
            <input
              className="admin-field__input"
              type="number"
              min="0"
              step="1"
              key={`allow-${config?.print_allowance}`}
              defaultValue={config?.print_allowance ?? 150}
              onBlur={(e) => commitNumber(
                e.target.value, config?.print_allowance, 'print_allowance', onSaveConfig)}
            />
          </label>
          <button
            className="printer-btn"
            onClick={() => commit({ prints_used: 0 })}
            disabled={used === 0}
          >
            Reset count
          </button>
        </div>

        <p className="printer-hint">
          Raising the allowance leaves the count alone; only Reset zeroes it.
        </p>

        <p className="printer-hint">
          Passed to <code>lp -o</code>, one option per space. <code>scaling=100</code> fills
          the page; the photo already carries a 300 dpi tag, so nothing has to guess its
          size. Both apply to the next print — no restart.
        </p>
      </section>

      {/* ═══ Test print ═══ */}
      <section className="printer-section">
        <h3 className="printer-section__title">Test Print</h3>
        <p className="printer-hint printer-hint--lead">
          Prints a 6×4 alignment card: a rule on the paper edge, half-inch ticks, and a
          true circle. Missing rule means bleed is being lost; an oval means the aspect
          is wrong. Only available from the idle screen.
        </p>

        <button className="printer-btn printer-btn--primary" onClick={runTest} disabled={busy}>
          {busy ? 'Printing…' : 'Print Alignment Card'}
        </button>

        {result && (
          <div className={`sys-action-result sys-action-result--${result.ok ? 'ok' : 'err'}`}>
            {result.ok
              ? 'Card printed — check the edges and the circle against a ruler'
              : (result.detail || 'Test print failed')}
          </div>
        )}
      </section>

      {/* ═══ Simulation ═══ */}
      {isMock && (
        <section className="printer-section">
          <h3 className="printer-section__title">Simulation</h3>
          <p className="printer-hint printer-hint--lead">
            Shapes the mock printer so the booth can be rehearsed against a real dye-sub's
            behaviour before one is attached. Set a fault, run a session, watch what the
            guest sees.
          </p>

          <div className="printer-mock-grid">
            <label className="admin-field">
              <span className="admin-field__label">Seconds per print</span>
              <input
                className="admin-field__input"
                type="number"
                min="0"
                step="0.5"
                key={`duration-${mock.job_duration_s}`}
                defaultValue={mock.job_duration_s ?? 13}
                onBlur={(e) =>
                  commitNumber(e.target.value, mock.job_duration_s, 'job_duration_s')}
              />
            </label>

            <label className="admin-field">
              <span className="admin-field__label">Prints on the roll</span>
              <input
                className="admin-field__input"
                type="number"
                min="0"
                step="1"
                key={`media-${mock.media_total}`}
                defaultValue={mock.media_total ?? 700}
                onBlur={(e) =>
                  commitNumber(e.target.value, mock.media_total, 'media_total')}
              />
            </label>
          </div>

          <label className="admin-field printer-field--wide">
            <span className="admin-field__label">Fault to simulate</span>
            <select
              className="admin-field__input"
              value={mock.fault || 'none'}
              onChange={(e) => onSaveMock({ fault: e.target.value })}
            >
              {MOCK_FAULTS.map((f) => (
                <option key={f.id} value={f.id}>{f.label}</option>
              ))}
            </select>
          </label>

          <p className="printer-hint">
            Changing the roll size reloads it. Set it low to watch the booth run out in a
            few prints rather than seven hundred.
          </p>
        </section>
      )}
    </div>
  );
}
