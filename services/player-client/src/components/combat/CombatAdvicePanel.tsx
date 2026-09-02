/**
 * LEG-46 — ARIA combat-advice callout during target selection (aria-companion.md § Combat advice).
 */
import React, { useEffect, useState } from 'react';
import { ariaCombatAdviceAPI } from '../../services/api';
import './combat-advice.css';

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Transport collapse copy is not gameserver detail (network-collapse densify). */
const isNetworkCollapseMessage = (msg: string): boolean => {
  const trimmed = msg.trim();
  return (
    !trimmed ||
    /^failed to fetch$/i.test(trimmed) ||
    /^network\s*error$/i.test(trimmed)
  );
};

export function formatCombatAdviceError(err: unknown): string {
  const fallback = 'ARIA combat advice unavailable';
  if (err instanceof TypeError) return fallback;
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !isNetworkCollapseMessage(message) &&
    !/^API Error: \d+$/.test(message.trim());

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'You do not have permission to load combat advice.';
  }

  if (status === 429) {
    return 'Combat advice rate limit exceeded — wait a moment and try again.';
  }

  if (status === 503 && hasServerDetail) return message!;
  if (hasServerDetail) return message!;
  return fallback;
}

interface CombatAdvicePanelProps {
  opponentShipType: string;
}

export const CombatAdvicePanel: React.FC<CombatAdvicePanelProps> = ({
  opponentShipType,
}) => {
  const [summary, setSummary] = useState<string | null>(null);
  const [weaponSuggestion, setWeaponSuggestion] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      setSummary(null);
      setWeaponSuggestion(null);
      try {
        const payload = await ariaCombatAdviceAPI.getAdvice(opponentShipType);
        if (cancelled) return;
        setSummary(payload.summary);
        setWeaponSuggestion(payload.weapon_suggestion ?? null);
      } catch (err) {
        if (!cancelled) setError(formatCombatAdviceError(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [opponentShipType]);

  return (
    <aside className="aria-combat-advice" aria-label="ARIA combat advice">
      <h4>ARIA COMBAT ADVICE</h4>
      {loading && <p className="loading">Reviewing your combat memories…</p>}
      {!loading && error && <p className="error" role="alert">{error}</p>}
      {!loading && !error && summary && <p>{summary}</p>}
      {!loading && !error && weaponSuggestion && (
        <p className="weapon">{weaponSuggestion}</p>
      )}
    </aside>
  );
};

export default CombatAdvicePanel;
