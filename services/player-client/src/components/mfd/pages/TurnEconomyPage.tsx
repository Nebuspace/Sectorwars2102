/**
 * TURN ECONOMY — MFD-A page (NEON15, zone B2).
 *
 * Field provenance (verified):
 *   playerState.{turns,max_turns,credits}  — PlayerState interface (GameContext.tsx)
 *   course.hops[currentHopIndex]           — CourseHop {name, turn_cost} (AutopilotContext.tsx)
 *
 * Turn regen shipped: turns refill continuously at a base rate of
 * 1000/86400 turns/sec (≈ 41.7/hr) scaled by the player's ARIA multiplier
 * (assumed 1.0 here — the client does not yet carry the multiplier), capped
 * at max_turns. TIME TO FULL is projected locally from a 1s ticker, mirroring
 * the prodNow stockpile-projection pattern in GameDashboard.tsx.
 */
import React, { useState, useEffect } from 'react';
import { useGame } from '../../../contexts/GameContext';
import { useAutopilot } from '../../../contexts/AutopilotContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDInsufficient } from '../atoms';
import { TurnsIcon } from '../../icons/TurnsIcon';
import './pages-ship.css';

const ACCENT = '#00FF7F';

// Base regen rate (ARIA multiplier assumed 1.0 — not carried by the client).
const REGEN_PER_SEC = 1000 / 86400;
const REGEN_PER_HR = REGEN_PER_SEC * 3600; // ≈ 41.67

const formatDuration = (totalSeconds: number): string => {
  const s = Math.max(0, Math.ceil(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (h > 0) return `${h}h ${pad(m)}m ${pad(sec)}s`;
  if (m > 0) return `${m}m ${pad(sec)}s`;
  return `${sec}s`;
};

const TurnEconomyPage: React.FC = () => {
  const { playerState } = useGame();
  const { course, currentHopIndex } = useAutopilot();

  // Live ticker (1s) for the TIME TO FULL countdown — mirrors GameDashboard's
  // prodNow projection pattern. Always mounted (hooks must run unconditionally).
  const [now, setNow] = useState<number>(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  if (!playerState) {
    return (
      <>
        <MFDPageHeader title="TURN ECONOMY" accent={ACCENT} status="shipped" />
        <MFDPageBody scrollKey="turn-economy">
          <MFDInsufficient />
        </MFDPageBody>
      </>
    );
  }

  const nextHop =
    course && currentHopIndex < course.hops.length ? course.hops[currentHopIndex] : null;

  const maxTurns = playerState.max_turns;
  const hasMax = typeof maxTurns === 'number';
  const isFull = hasMax && playerState.turns >= (maxTurns as number);
  const remainingTurns = hasMax ? (maxTurns as number) - playerState.turns : 0;

  // The ticker is referenced so the countdown re-renders each second; the
  // projection itself is from current turns (server is authoritative on poll).
  void now;
  let timeToFull = '—';
  if (hasMax) {
    if (isFull) {
      timeToFull = 'FULL';
    } else {
      timeToFull = formatDuration(remainingTurns / REGEN_PER_SEC);
    }
  }

  // WO-PROG-TURN-VISIBILITY: scarcity warning per canon (turns.md "Low-turn
  // warning UI hints when the pool is below thresholds (design: <50)"). The
  // hint reuses the same remainingTurns/REGEN_PER_SEC math as timeToFull
  // above, just rounded to whole hours for a compact one-liner.
  const lowTurns = playerState.turns < 50;
  const lowTurnsHint =
    lowTurns && hasMax && !isFull
      ? `low turns — regen in ${
          remainingTurns / REGEN_PER_SEC / 3600 < 1
            ? '<1h'
            : `${Math.round(remainingTurns / REGEN_PER_SEC / 3600)}h`
        }`
      : null;

  return (
    <>
      <MFDPageHeader title="TURN ECONOMY" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="turn-economy">
        <div className="mfd-page-fields">
          <MFDField
            label="TURNS"
            value={
              lowTurns ? (
                <span className="mfd-value-caution">{playerState.turns.toLocaleString()}</span>
              ) : (
                playerState.turns.toLocaleString()
              )
            }
            accent
          />
          {hasMax && (
            <MFDField label="MAX TURNS" value={(maxTurns as number).toLocaleString()} />
          )}
          <MFDField label="REGEN" value={`${REGEN_PER_HR.toFixed(1)} / HR`} />
          <MFDField
            label="TIME TO FULL"
            value={timeToFull}
            accent={hasMax && !isFull}
          />
          <MFDField label="CREDITS" value={playerState.credits.toLocaleString()} />
          <MFDField label="NEXT HOP" value={nextHop ? nextHop.name : '—'} />
          <MFDField
            label="HOP COST"
            value={nextHop ? <><TurnsIcon /> {nextHop.turn_cost}</> : '—'}
            accent={nextHop !== null}
          />
        </div>
        {lowTurnsHint && (
          <div className="mfd-page-cautionline" role="status">
            {lowTurnsHint}
          </div>
        )}
      </MFDPageBody>
    </>
  );
};

export default React.memo(TurnEconomyPage);
