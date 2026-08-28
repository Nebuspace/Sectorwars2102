/**
 * LEG-785 — player-controlled ARIA assistance level (ADR-0068).
 * GET/PUT /api/v1/ai/profile via existing aiTradingService. Volunteering
 * only — does not reset relationship/consciousness. No medium.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  AI_ASSISTANCE_LEVELS,
  type AIAssistanceLevel,
} from '../ai/types';
import { aiTradingService } from '../../services/aiTradingService';
import './assistance-level.css';

const CANON = new Set<string>(AI_ASSISTANCE_LEVELS);

export const coerceAssistanceLevel = (value: unknown): AIAssistanceLevel => {
  if (typeof value === 'string' && CANON.has(value)) {
    return value as AIAssistanceLevel;
  }
  return 'standard';
};

const LEVEL_LABEL: Record<AIAssistanceLevel, string> = {
  minimal: 'Minimal',
  quiet: 'Quiet',
  standard: 'Standard',
  full: 'Full',
};

const AssistanceLevelSettings: React.FC = () => {
  const [level, setLevel] = useState<AIAssistanceLevel>('standard');
  const [riskTolerance, setRiskTolerance] = useState(0.5);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const profile = await aiTradingService.getTradingProfile();
      setLevel(coerceAssistanceLevel(profile?.ai_assistance_level));
      setRiskTolerance(
        typeof profile?.risk_tolerance === 'number' ? profile.risk_tolerance : 0.5,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load ARIA assistance level');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  const onChange = async (next: string) => {
    const coerced = coerceAssistanceLevel(next);
    if (next === 'medium' || !CANON.has(next)) {
      setError('Assistance level must be minimal, quiet, standard, or full.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await aiTradingService.updateAIPreferences({
        ai_assistance_level: coerced,
        risk_tolerance: riskTolerance,
      });
      setLevel(coerced);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update ARIA assistance level');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="aria-assistance-settings">
      <label className="aria-assistance-label" htmlFor="aria-assistance-level">
        Assistance level
      </label>
      <p className="aria-assistance-hint">
        How often ARIA volunteers help. Does not change learning depth.
      </p>
      <select
        id="aria-assistance-level"
        className="aria-assistance-select"
        value={level}
        disabled={loading || saving}
        onChange={(event) => {
          void onChange(event.target.value);
        }}
      >
        {AI_ASSISTANCE_LEVELS.map((value) => (
          <option key={value} value={value}>
            {LEVEL_LABEL[value]}
          </option>
        ))}
      </select>
      {loading && <p className="aria-assistance-status">Loading profile…</p>}
      {saving && !loading && <p className="aria-assistance-status">Saving…</p>}
      {error && (
        <p className="aria-assistance-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
};

export default AssistanceLevelSettings;
