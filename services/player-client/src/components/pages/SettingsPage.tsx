import React from 'react';
import { useNavigate } from 'react-router-dom';
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

// UI scale bounds for the slider (fractions). Default 1.0 = 100%.
const UI_SCALE_MIN = 0.6;
const UI_SCALE_MAX = 1.2;
const UI_SCALE_STEP = 0.05;

const SettingsPage: React.FC = () => {
  const { uiScale, setUiScale } = useSettings();
  const navigate = useNavigate();

  const handleScaleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const next = parseFloat(e.target.value);
    if (Number.isFinite(next)) setUiScale(next);
  };

  // Current scale as a whole-percent string for the live readout.
  const currentPercent = `${Math.round(uiScale * 100)}%`;

  return (
      <CockpitInstrument
        title="SETTINGS"
        accent="#00D9FF"
        subtitle={'CLIENT CONFIGURATION'}
      >
        <div className="settings-page">
          {/* Exit control — without this a player who opens /game/settings is
              trapped (must use browser-back). Returns to the cockpit. */}
          <button
            type="button"
            className="settings-back-btn"
            onClick={() => navigate('/game')}
            title="Return to the game"
          >
            ← Back to game
          </button>

          <section className="settings-section">
            <div className="settings-section-header">
              <h3 className="settings-section-title">DISPLAY</h3>
            </div>

            <div className="settings-row">
              <div className="settings-row-label">
                <label htmlFor="ui-scale-range" className="settings-label">
                  UI Scale
                </label>
                <p className="settings-hint">
                  Scale the entire interface up or down (60%–120%). Applies instantly.
                </p>
              </div>
              <div className="settings-row-control">
                <input
                  id="ui-scale-range"
                  type="range"
                  className="settings-range"
                  min={UI_SCALE_MIN}
                  max={UI_SCALE_MAX}
                  step={UI_SCALE_STEP}
                  value={uiScale}
                  onChange={handleScaleChange}
                  aria-valuetext={currentPercent}
                />
                <span className="settings-current-value" aria-live="polite">
                  {currentPercent}
                </span>
              </div>
            </div>
          </section>
        </div>
      </CockpitInstrument>
  );
};

export default SettingsPage;
