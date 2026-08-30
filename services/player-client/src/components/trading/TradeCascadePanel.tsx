import React, { useState } from 'react';
import { useGame } from '../../contexts/GameContext';
import { formatCredits } from '../../utils/formatters';
import {
  ariaTradeCascadeAPI,
  isTradeCascadeRefusal,
  TradeCascadePlan,
} from '../../services/api';
import './trade-cascade.css';

function httpStatus(err: unknown): number | undefined {
  if (err && typeof err === 'object') {
    const direct = (err as { status?: number }).status;
    if (typeof direct === 'number') return direct;
    const resp = (err as { response?: { status?: number } }).response;
    if (typeof resp?.status === 'number') return resp.status;
  }
  return undefined;
}

/** Surface GS trade-cascade plan detail; hide bare API Error: N blobs (LEG-2957). */
export function formatTradeCascadeError(err: unknown): string {
  const status = httpStatus(err);
  const message = err instanceof Error ? err.message : undefined;
  const hasServerDetail =
    typeof message === 'string' &&
    message.trim().length > 0 &&
    !/^API Error: \d+$/.test(message.trim());

  if (status === 403) {
    if (hasServerDetail) return message!;
    return 'Access denied — you cannot plan a trade cascade right now.';
  }

  if (status === 429) {
    return 'Trade cascade rate limit exceeded — wait a moment and try again.';
  }

  if (hasServerDetail) return message!;
  return 'Failed to plan trade cascade.';
}

/**
 * LEG-725 — first player consumer of POST /api/v1/ai/trade-cascade.
 * Plans multi-hop trade cascades through explored sectors only; refusal
 * payloads are service-owned (no fabricated routes).
 */
const TradeCascadePanel: React.FC = () => {
  const { playerState } = useGame();

  const [collapsed, setCollapsed] = useState(true);
  const [startSector, setStartSector] = useState(
    playerState?.current_sector_id ? String(playerState.current_sector_id) : '',
  );
  const [targetProfit, setTargetProfit] = useState('1000');
  const [maxJumps, setMaxJumps] = useState('5');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refusalMessage, setRefusalMessage] = useState<string | null>(null);
  const [refusalSuggestion, setRefusalSuggestion] = useState<string | null>(null);
  const [plan, setPlan] = useState<TradeCascadePlan | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;

    const start = startSector.trim();
    const profit = Number(targetProfit);
    const jumps = Number(maxJumps);

    if (!start) {
      setError('Start sector is required.');
      return;
    }
    if (!Number.isFinite(profit) || profit <= 0) {
      setError('Target profit must be greater than zero.');
      return;
    }
    if (!Number.isInteger(jumps) || jumps < 1 || jumps > 20) {
      setError('Max jumps must be an integer from 1 to 20.');
      return;
    }

    setLoading(true);
    setError(null);
    setRefusalMessage(null);
    setRefusalSuggestion(null);
    setPlan(null);

    try {
      const response = await ariaTradeCascadeAPI.planTradeCascade({
        start_sector_id: start,
        target_profit: profit,
        max_jumps: jumps,
      });

      if (isTradeCascadeRefusal(response)) {
        setRefusalMessage(response.message);
        setRefusalSuggestion(response.suggestion ?? null);
        return;
      }

      setPlan(response);
    } catch (err: unknown) {
      setError(formatTradeCascadeError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="trade-cascade-panel">
      <div
        className="trade-cascade-header"
        onClick={() => setCollapsed(!collapsed)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setCollapsed(!collapsed);
          }
        }}
      >
        <h3>ARIA Trade Cascade</h3>
        <span className="trade-cascade-toggle">{collapsed ? '▶' : '▼'}</span>
      </div>
      <p className="trade-cascade-subtitle" id="trade-cascade-subtitle">
        Multi-hop routes through explored sectors only — not the graph route optimizer.
      </p>

      {!collapsed && (
        <div className="trade-cascade-body" aria-labelledby="trade-cascade-subtitle">
          <form className="trade-cascade-form" onSubmit={handleSubmit}>
            <label>
              Start sector ID
              <input
                type="text"
                value={startSector}
                onChange={(e) => setStartSector(e.target.value)}
                aria-label="Start sector ID"
              />
            </label>
            <label>
              Target profit (credits)
              <input
                type="number"
                min={1}
                step={1}
                value={targetProfit}
                onChange={(e) => setTargetProfit(e.target.value)}
                aria-label="Target profit in credits"
              />
            </label>
            <label>
              Max jumps (1–20)
              <input
                type="number"
                min={1}
                max={20}
                step={1}
                value={maxJumps}
                onChange={(e) => setMaxJumps(e.target.value)}
                aria-label="Maximum jumps"
              />
            </label>
            <div className="trade-cascade-actions">
              <button type="submit" disabled={loading}>
                {loading ? 'Planning…' : 'Plan cascade'}
              </button>
            </div>
          </form>

          {error && (
            <p className="trade-cascade-error" role="alert">
              {error}
            </p>
          )}

          {refusalMessage && (
            <div className="trade-cascade-refusal" role="status">
              <p>{refusalMessage}</p>
              {refusalSuggestion && <p>{refusalSuggestion}</p>}
            </div>
          )}

          {plan && (
            <div className="trade-cascade-result" role="region" aria-label="Trade cascade plan">
              <dl>
                <dt>Total profit</dt>
                <dd>{formatCredits(plan.total_profit)}</dd>
                <dt>Jumps</dt>
                <dd>{plan.total_jumps}</dd>
                <dt>Profit per jump</dt>
                <dd>{formatCredits(plan.profit_per_jump)}</dd>
                <dt>Confidence</dt>
                <dd>{(plan.confidence * 100).toFixed(0)}%</dd>
              </dl>
              {plan.steps.length > 0 && (
                <ol className="trade-cascade-steps">
                  {plan.steps.map((step) => (
                    <li key={`${step.step}-${step.sector}-${step.commodity}`}>
                      Step {step.step}: {step.action} {step.commodity} @ sector {step.sector}
                      {' — '}
                      {formatCredits(step.expected_price)} ({step.based_on})
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default TradeCascadePanel;
