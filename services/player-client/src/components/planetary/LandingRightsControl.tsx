/**
 * LandingRightsControl — owner-only ACL setter for colonized planets (LEG-155).
 *
 * Canon: FEATURES/planets/colonization.md — five modes (public / team_only /
 * private / whitelist / denylist). Backend PUT /planets/{id}/landing-rights
 * already enforces all five; this control wires the three modes that need no
 * UUID list editor. Whitelist/denylist stay visible but disabled until a
 * follow-up ships a minimal list editor.
 */
import React, { useEffect, useState } from 'react';
import { planetaryAPI } from '../../services/api';
import './landing-rights-control.css';

export type LandingRightsMode =
  | 'public'
  | 'team_only'
  | 'private'
  | 'whitelist'
  | 'denylist';

const SIMPLE_MODES: LandingRightsMode[] = ['public', 'team_only', 'private'];

const MODE_OPTIONS: Array<{
  value: LandingRightsMode;
  label: string;
  enabled: boolean;
  disabledReason?: string;
}> = [
  { value: 'public', label: 'Public — anyone may land', enabled: true },
  { value: 'team_only', label: 'Team only — teammates may land', enabled: true },
  { value: 'private', label: 'Private — owner only', enabled: true },
  {
    value: 'whitelist',
    label: 'Whitelist — named players only',
    enabled: false,
    disabledReason: 'UUID whitelist editor not in this release — use public / team only / private.',
  },
  {
    value: 'denylist',
    label: 'Denylist — block named players',
    enabled: false,
    disabledReason: 'UUID denylist editor not in this release — use public / team only / private.',
  },
];

function normalizeMode(raw: unknown): LandingRightsMode {
  if (typeof raw === 'string' && MODE_OPTIONS.some((o) => o.value === raw)) {
    return raw as LandingRightsMode;
  }
  return 'public';
}

export interface LandingRightsControlProps {
  planetId: string;
  /** When false, render nothing (non-owner / not landed on own colony). */
  isOwned: boolean;
  /** Seed from detail payload when present; otherwise canon default `public`. */
  initialMode?: string | null;
  onChanged?: (mode: LandingRightsMode) => void;
}

export const LandingRightsControl: React.FC<LandingRightsControlProps> = ({
  planetId,
  isOwned,
  initialMode,
  onChanged,
}) => {
  const [mode, setMode] = useState<LandingRightsMode>(() => normalizeMode(initialMode));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setMode(normalizeMode(initialMode));
    setError(null);
    setNotice(null);
  }, [planetId, initialMode]);

  if (!isOwned) return null;

  const applyMode = async (next: LandingRightsMode) => {
    if (!SIMPLE_MODES.includes(next) || next === mode || saving) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await planetaryAPI.setLandingRights(planetId, {
        mode: next,
        whitelist: [],
        denylist: [],
      });
      const applied = normalizeMode(result?.mode ?? next);
      setMode(applied);
      setNotice(result?.message || `Landing rights set to ${applied}.`);
      onChanged?.(applied);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to update landing rights.';
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="landing-rights-control"
      data-testid="landing-rights-control"
      role="group"
      aria-label="Landing rights"
    >
      <label className="landing-rights-label" htmlFor={`landing-rights-${planetId}`}>
        Landing rights
      </label>
      <select
        id={`landing-rights-${planetId}`}
        className="landing-rights-select"
        data-testid="landing-rights-select"
        value={SIMPLE_MODES.includes(mode) ? mode : 'public'}
        disabled={saving}
        aria-busy={saving}
        onChange={(e) => {
          void applyMode(e.target.value as LandingRightsMode);
        }}
      >
        {MODE_OPTIONS.map((opt) => (
          <option
            key={opt.value}
            value={opt.value}
            disabled={!opt.enabled}
            title={opt.disabledReason}
          >
            {opt.enabled ? opt.label : `${opt.label} (unavailable)`}
          </option>
        ))}
      </select>
      {saving && (
        <span className="landing-rights-status" data-testid="landing-rights-saving">
          Saving…
        </span>
      )}
      {error && (
        <span className="landing-rights-error" role="alert" data-testid="landing-rights-error">
          {error}
        </span>
      )}
      {notice && !error && (
        <span className="landing-rights-notice" role="status" data-testid="landing-rights-notice">
          {notice}
        </span>
      )}
      <p className="landing-rights-hint" data-testid="landing-rights-list-residual">
        Whitelist / denylist modes need a player-UUID list editor — not available here yet.
      </p>
    </div>
  );
};

export default LandingRightsControl;
