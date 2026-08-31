import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CombatActivityChart } from './CombatActivityChart';

function recentStartedAt(): string {
  return new Date().toISOString();
}

const validEvent = {
  id: 'combat-1',
  started_at: recentStartedAt(),
  combat_stats: {
    damageDealt: 1200,
    damageReceived: 800,
  },
};

describe('CombatActivityChart defensive render (LEG-3132)', () => {
  it('renders empty events with SVG mounted', () => {
    render(<CombatActivityChart events={[]} />);

    expect(screen.getByText('Combat Activity (Last Hour)')).toBeTruthy();
    const svg = document.querySelector('.combat-activity-chart svg');
    expect(svg).toBeTruthy();
  });

  it('renders a valid CombatFeedItem-shaped event without throwing', () => {
    expect(() => render(<CombatActivityChart events={[validEvent]} />)).not.toThrow();

    const svg = document.querySelector('.combat-activity-chart svg');
    expect(svg).toBeTruthy();
  });

  it('defensively renders partial payloads missing started_at/combat_stats', () => {
    const partialEvents = [
      { id: 'partial-1' },
      { id: 'partial-2', started_at: 'not-a-date' },
      { id: 'partial-3', combat_stats: { damageDealt: 100 } },
      { id: 'partial-4', started_at: null, combat_stats: null },
    ];

    expect(() => render(<CombatActivityChart events={partialEvents} />)).not.toThrow();

    const svg = document.querySelector('.combat-activity-chart svg');
    expect(svg).toBeTruthy();
  });
});
