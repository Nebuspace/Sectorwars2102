import React from 'react';
import GameLayout from '../layouts/GameLayout';
import CockpitInstrument from '../cockpit/CockpitInstrument';
import { useSettings } from '../../contexts/SettingsContext';
import './settings-page.css';

/**
 * SettingsPage — the client CONFIGURATION console.
 *
 * Dedicated screen for purely-local player preferences (no server round-trip).
 * Sectioned so future prefs (audio, accessibility, notifications, etc.) slot
 * in as additional <section> blocks without restructuring. The first section,
 * Display, hosts the global UI Scale control which applies live and persists.
 */

/** UI-scale presets, as {label, fraction} pairs. */
const UI_SCALE_OPTIONS: { label: string; value: number }[] = [
  { label: '80%', value: 0.8 },
  { label: '90%', value: 0.9 },
  { label: '100%', value: 1.0 },
  { label: '110%', value: 1.1 },
  { label: '125%', value: 1.25 },
  { label: '150%', value: 1.5 },
];

const SettingsPage: React.FC = () => {
  const { uiScale, setUiScale } = useSettings();

  const handleScaleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const next = parseFloat(e.target.value);
    if (Number.isFinite(next)) setUiScale(next);
  };

  // Current scale as a whole-percent string for the live readout.
  const currentPercent = `${Math.round(uiScale * 100)}%`;

  return (
    <GameLayout>
      <CockpitInstrument
        title="SETTINGS"
        accent="#00D9FF"
        subtitle={'CLIENT CONFIGURATION'}
      >
        <div className="settings-page">
          <section className="settings-section">
            <div className="settings-section-header">
              <h3 className="settings-section-title">DISPLAY</h3>
            </div>

            <div className="settings-row">
              <div className="settings-row-label">
                <label htmlFor="ui-scale-select" className="settings-label">
                  UI Scale
                </label>
                <p className="settings-hint">
                  Scale the entire interface up or down. Applies instantly.
                </p>
              </div>
              <div className="settings-row-control">
                <select
                  id="ui-scale-select"
                  className="settings-select"
                  value={uiScale}
                  onChange={handleScaleChange}
                >
                  {UI_SCALE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <span className="settings-current-value" aria-live="polite">
                  {currentPercent}
                </span>
              </div>
            </div>
          </section>
        </div>
      </CockpitInstrument>
    </GameLayout>
  );
};

export default SettingsPage;
