import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CombatFeed } from './CombatFeed';

const fullEvent = {
  id: 'combat-1',
  combat_type: 'ship_vs_ship',
  status: 'completed',
  started_at: '2026-08-30T12:00:00Z',
  ended_at: '2026-08-30T12:05:00Z',
  duration_seconds: 300,
  current_round: 3,
  sector: { name: 'Sector 42', id: 'sec-42' },
  attacker: { id: 'a1', type: 'fighter', name: 'Alpha', level: 5 },
  defender: { id: 'd1', type: 'freighter', name: 'Beta', level: 2 },
  victor_id: 'a1',
  is_active: false,
  needs_intervention: false,
};

describe('CombatFeed defensive render (LEG-3126)', () => {
  it('shows empty state when no events', () => {
    render(<CombatFeed events={[]} />);

    expect(screen.getByText('Live Combat Feed')).toBeTruthy();
    expect(screen.getByText('0 battles')).toBeTruthy();
    expect(screen.getByText('No active combat.')).toBeTruthy();
  });

  it('renders a complete event with attacker win label', () => {
    render(<CombatFeed events={[fullEvent]} />);

    expect(screen.getByText('1 battles')).toBeTruthy();
    expect(screen.getByText('ATTACKER WINS')).toBeTruthy();
    expect(screen.getByText(/Alpha L5/)).toBeTruthy();
    expect(screen.getByText(/Beta L2/)).toBeTruthy();
    expect(screen.getByText('Sector 42')).toBeTruthy();
    expect(screen.getByText(/Duration: 5m 0s/)).toBeTruthy();
  });

  it('defensively renders partial payloads without throwing (LEG-3126)', () => {
    const partialEvents = [
      {
        id: 'partial-1',
        combat_type: 'unknown',
        status: 'active',
        started_at: '',
        duration_seconds: NaN as unknown as number,
        current_round: 0,
        attacker: undefined,
        defender: null,
        victor_id: null,
        is_active: true,
        needs_intervention: true,
      },
      {
        id: 'partial-2',
        combat_type: 'drone',
        status: 'ended',
        started_at: 'not-a-date',
        duration_seconds: -5,
        current_round: 1,
        attacker: { id: 'x', type: 'drone', name: 'Solo' },
        defender: { id: 'y', type: 'station', name: 'Outpost' },
        victor_id: 'unknown-id',
        is_active: false,
        needs_intervention: false,
      },
    ];

    expect(() => render(<CombatFeed events={partialEvents} />)).not.toThrow();

    expect(screen.getByText('IN PROGRESS')).toBeTruthy();
    expect(screen.getByText('DRAW')).toBeTruthy();
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0);
    expect(screen.getByText('NEEDS INTERVENTION')).toBeTruthy();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('invokes dispute and intervention callbacks with event id', () => {
    const onDisputeClick = vi.fn();
    const onInterventionClick = vi.fn();

    render(
      <CombatFeed
        events={[fullEvent]}
        onDisputeClick={onDisputeClick}
        onInterventionClick={onInterventionClick}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Dispute/i }));
    fireEvent.click(screen.getByRole('button', { name: /Intervene/i }));

    expect(onDisputeClick).toHaveBeenCalledWith('combat-1');
    expect(onInterventionClick).toHaveBeenCalledWith('combat-1');
  });
});
