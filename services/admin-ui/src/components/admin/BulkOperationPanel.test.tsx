import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import BulkOperationPanel from './BulkOperationPanel';
import type { PlayerModel } from '../../types/playerManagement';

function makePlayer(id: string, username: string): PlayerModel {
  return {
    id,
    username,
    email: `${username}@example.com`,
    credits: 1_000,
    turns: 10,
    current_sector_id: 1,
    current_region_id: null,
    current_ship_id: null,
    team_id: null,
    is_active: true,
    last_login: null,
    created_at: '2026-01-01T00:00:00Z',
    ships_count: null,
    planets_count: null,
    stations_count: null,
    status: 'active',
    assets: {
      ships_count: null,
      planets_count: null,
      stations_count: null,
      total_value: null,
    },
    activity: {
      last_login: null,
      session_count_today: null,
      actions_today: null,
      total_trade_volume: null,
      combat_rating: null,
      suspicious_activity: false,
    },
    aria: null,
  };
}

describe('BulkOperationPanel honesty (LEG-3129)', () => {
  it('shows honesty note and empty selection copy with no fake operation cards', () => {
    render(
      <BulkOperationPanel
        selectedPlayers={[]}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );

    const note = screen.getByRole('note');
    expect(note.textContent).toMatch(/POST \/api\/v1\/admin\/players\/bulk-operation/);
    expect(note.textContent).toMatch(/not implemented/i);
    expect(screen.getByText('No players selected.')).toBeTruthy();
    expect(screen.getByText('0 players selected')).toBeTruthy();
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /Execute/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Adjust Credits/i })).toBeNull();
  });

  it('lists all selected players when count is 3', () => {
    const players = [
      makePlayer('p1', 'Alpha'),
      makePlayer('p2', 'Bravo'),
      makePlayer('p3', 'Charlie'),
    ];

    render(
      <BulkOperationPanel
        selectedPlayers={players}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );

    expect(screen.getByText('3 players selected')).toBeTruthy();
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(screen.getByText('Bravo')).toBeTruthy();
    expect(screen.getByText('Charlie')).toBeTruthy();
    expect(screen.queryByText(/more players/i)).toBeNull();
  });

  it('truncates list at 10 and shows overflow count for 12 players', () => {
    const players = Array.from({ length: 12 }, (_, index) =>
      makePlayer(`p${index + 1}`, `Player${index + 1}`),
    );

    render(
      <BulkOperationPanel
        selectedPlayers={players}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );

    expect(screen.getByText('12 players selected')).toBeTruthy();
    expect(screen.getByText('Player1')).toBeTruthy();
    expect(screen.getByText('Player10')).toBeTruthy();
    expect(screen.queryByText('Player11')).toBeNull();
    expect(screen.getByText('...and 2 more players')).toBeTruthy();
  });

  it('invokes onClose from the footer Close button', () => {
    const onClose = vi.fn();

    render(
      <BulkOperationPanel
        selectedPlayers={[]}
        onClose={onClose}
        onComplete={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('invokes onClose from the header × button', () => {
    const onClose = vi.fn();

    render(
      <BulkOperationPanel
        selectedPlayers={[]}
        onClose={onClose}
        onComplete={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
