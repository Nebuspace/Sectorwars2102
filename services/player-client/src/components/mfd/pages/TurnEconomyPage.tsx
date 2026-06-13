/**
 * TURN ECONOMY — MFD-A page (NEON15, zone B2).
 *
 * Field provenance (verified):
 *   playerState.{turns,credits}        — PlayerState interface (GameContext.tsx)
 *   course.hops[currentHopIndex]       — CourseHop {name, turn_cost} (AutopilotContext.tsx)
 * PlayerState has NO max_turns field — it is intentionally not rendered, and
 * there is no time-to-full projection (turn regen is not shipped).
 */
import React from 'react';
import { useGame } from '../../../contexts/GameContext';
import { useAutopilot } from '../../../contexts/AutopilotContext';
import { MFDPageHeader, MFDPageBody, MFDField, MFDInsufficient } from '../atoms';
import './pages-ship.css';

const ACCENT = '#00FF7F';

const TurnEconomyPage: React.FC = () => {
  const { playerState } = useGame();
  const { course, currentHopIndex } = useAutopilot();

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

  return (
    <>
      <MFDPageHeader title="TURN ECONOMY" accent={ACCENT} status="shipped" />
      <MFDPageBody scrollKey="turn-economy">
        <div className="mfd-page-fields">
          <MFDField label="TURNS" value={playerState.turns.toLocaleString()} accent />
          <MFDField label="CREDITS" value={playerState.credits.toLocaleString()} />
          <MFDField label="NEXT HOP" value={nextHop ? nextHop.name : '—'} />
          <MFDField
            label="HOP COST"
            value={nextHop ? `${nextHop.turn_cost} T` : '—'}
            accent={nextHop !== null}
          />
        </div>
      </MFDPageBody>
    </>
  );
};

export default React.memo(TurnEconomyPage);
