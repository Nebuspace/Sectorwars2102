/**
 * LandingRightsControl — owner-only ACL setter for colonized planets (LEG-155 + LEG-INI-31).
 *
 * Canon: FEATURES/planets/colonization.md — five modes (public / team_only /
 * private / whitelist / denylist). Backend PUT /planets/{id}/landing-rights
 * already enforces all five. LEG-INI-31 wires whitelist/denylist with a
 * minimal UUID list editor matching the PUT body shape.
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

const MODE_OPTIONS: Array<{
  value: LandingRightsMode;
  label: string;
}> = [
  { value: 'public', label: 'Public — anyone may land' },
  { value: 'team_only', label: 'Team only — teammates may land' },
  { value: 'private', label: 'Private — owner only' },
  { value: 'whitelist', label: 'Whitelist — named players only' },
  { value: 'denylist', label: 'Denylist — block named players' },
];

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function normalizeMode(raw: unknown): LandingRightsMode {
  if (typeof raw === 'string' && MODE_OPTIONS.some((o) => o.value === raw)) {
    return raw as LandingRightsMode;
  }
  return 'public';
}

/** Parse UUID lines / commas / whitespace; drop empties; reject invalids. */
export function parseUuidList(raw: string): { ok: string[]; bad: string[] } {
  const tokens = raw
    .split(/[\s,;]+/)
    .map((t) => t.trim())
    .filter(Boolean);
  const ok: string[] = [];
  const bad: string[] = [];
  const seen = new Set<string>();
  for (const token of tokens) {
    if (!UUID_RE.test(token)) {
      bad.push(token);
      continue;
    }
    const key = token.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    ok.push(token);
  }
  return { ok, bad };
}

export interface LandingRightsControlProps {
  planetId: string;
  /** When false, render nothing (non-owner / not landed on own colony). */
  isOwned: boolean;
  /** Seed from detail payload when present; otherwise canon default `public`. */
  initialMode?: string | null;
  /** Optional seed lists from detail payload. */
  initialWhitelist?: string[] | null;
  initialDenylist?: string[] | null;
  onChanged?: (mode: LandingRightsMode) => void;
}

export const LandingRightsControl: React.FC<LandingRightsControlProps> = ({
  planetId,
  isOwned,
  initialMode,
  initialWhitelist,
  initialDenylist,
  onChanged,
}) => {
  const [mode, setMode] = useState<LandingRightsMode>(() => normalizeMode(initialMode));
  const [listDraft, setListDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const next = normalizeMode(initialMode);
    setMode(next);
    setError(null);
    setNotice(null);
    if (next === 'whitelist') {
      setListDraft((initialWhitelist ?? []).join('\n'));
    } else if (next === 'denylist') {
      setListDraft((initialDenylist ?? []).join('\n'));
    } else {
      setListDraft('');
    }
  }, [planetId, initialMode, initialWhitelist, initialDenylist]);

  if (!isOwned) return null;

  const needsList = mode === 'whitelist' || mode === 'denylist';

  const applyMode = async (next: LandingRightsMode, listOverride?: string) => {
    if (saving) return;
    if (next === mode && listOverride === undefined && !needsList) return;

    let whitelist: string[] = [];
    let denylist: string[] = [];

    if (next === 'whitelist' || next === 'denylist') {
      const parsed = parseUuidList(listOverride ?? listDraft);
      if (parsed.bad.length > 0) {
        setError(`Invalid UUID(s): ${parsed.bad.slice(0, 3).join(', ')}`);
        setNotice(null);
        return;
      }
      if (parsed.ok.length === 0) {
        setError(
          next === 'whitelist'
            ? 'Whitelist needs at least one player UUID (empty list is fail-closed).'
            : 'Denylist needs at least one player UUID.',
        );
        setNotice(null);
        return;
      }
      if (next === 'whitelist') whitelist = parsed.ok;
      else denylist = parsed.ok;
    }

    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await planetaryAPI.setLandingRights(planetId, {
        mode: next,
        whitelist,
        denylist,
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
        value={mode}
        disabled={saving}
        aria-busy={saving}
        onChange={(e) => {
          const next = e.target.value as LandingRightsMode;
          if (next === 'whitelist' || next === 'denylist') {
            setMode(next);
            setError(null);
            setNotice(null);
            return;
          }
          void applyMode(next);
        }}
      >
        {MODE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>

      {needsList && (
        <div className="landing-rights-list-editor" data-testid="landing-rights-list-editor">
          <label
            className="landing-rights-list-label"
            htmlFor={`landing-rights-list-${planetId}`}
          >
            {mode === 'whitelist' ? 'Allowed player UUIDs' : 'Blocked player UUIDs'}
          </label>
          <textarea
            id={`landing-rights-list-${planetId}`}
            className="landing-rights-list-input"
            data-testid="landing-rights-list-input"
            rows={3}
            placeholder="One UUID per line (or comma-separated)"
            value={listDraft}
            disabled={saving}
            onChange={(e) => setListDraft(e.target.value)}
            aria-describedby={`landing-rights-list-hint-${planetId}`}
          />
          <p
            id={`landing-rights-list-hint-${planetId}`}
            className="landing-rights-hint"
            data-testid="landing-rights-list-hint"
          >
            Paste player UUIDs. Empty list is rejected (fail-closed).
          </p>
          <button
            type="button"
            className="landing-rights-apply"
            data-testid="landing-rights-apply-list"
            disabled={saving}
            onClick={() => void applyMode(mode, listDraft)}
          >
            {saving ? 'Saving…' : `Apply ${mode}`}
          </button>
        </div>
      )}

      {saving && !needsList && (
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
    </div>
  );
};

export default LandingRightsControl;
