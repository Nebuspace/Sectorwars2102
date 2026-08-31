import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import EmergencyOperationsPanel from './EmergencyOperationsPanel';
import type { PlayerModel } from '../../types/playerManagement';

function makePlayer(overrides: Partial<PlayerModel> = {}): PlayerModel {
  return {
    id: 'p1',
    username: 'Trader',
    email: 'trader@example.com',
    credits: 100,
    turns: 10,
    current_sector_id: 42,
    current_region_id: null,
    current_ship_id: null,
    team_id: null,
    is_active: true,
    last_login: '2026-01-15T12:00:00Z',
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
      last_login: '2026-01-15T12:00:00Z',
      session_count_today: null,
      actions_today: null,
      total_trade_volume: null,
      combat_rating: null,
      suspicious_activity: false,
    },
    aria: null,
    ...overrides,
  };
}

describe('EmergencyOperationsPanel honesty (LEG-3130)', () => {
  it('shows honesty note citing both missing endpoints and no fake operation cards', () => {
    render(
      <EmergencyOperationsPanel
        player={makePlayer()}
        onClose={() => {}}
        onUpdate={() => {}}
      />,
    );

    const note = screen.getByRole('note');
    expect(note.textContent).toMatch(/POST \/api\/v1\/admin\/players\/emergency-operation/);
    expect(note.textContent).toMatch(/GET \/api\/v1\/admin\/players\/\{id\}\/extended/);
    expect(note.textContent).toMatch(/not implemented/i);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /Execute/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /teleport/i })).toBeNull();
  });

  it('renders Current Status fields from the player fixture', () => {
    render(
      <EmergencyOperationsPanel
        player={makePlayer()}
        onClose={() => {}}
        onUpdate={() => {}}
      />,
    );

    expect(screen.getByText('Current Status')).toBeTruthy();
    expect(screen.getByText('Sector 42')).toBeTruthy();
    expect(screen.getByText('100')).toBeTruthy();
    expect(screen.getByText('10')).toBeTruthy();
    expect(screen.getByText(/1\/15\/2026/i)).toBeTruthy();
  });

  it('applies negative class for negative credits and positive class otherwise', () => {
    const { rerender } = render(
      <EmergencyOperationsPanel
        player={makePlayer({ credits: -500 })}
        onClose={() => {}}
        onUpdate={() => {}}
      />,
    );

    const negativeCredits = screen.getByText('-500');
    expect(negativeCredits.className).toMatch(/negative/);

    rerender(
      <EmergencyOperationsPanel
        player={makePlayer({ credits: 250 })}
        onClose={() => {}}
        onUpdate={() => {}}
      />,
    );

    const positiveCredits = screen.getByText('250');
    expect(positiveCredits.className).toMatch(/positive/);
  });

  it('applies low class when turns are below 10', () => {
    render(
      <EmergencyOperationsPanel
        player={makePlayer({ turns: 3 })}
        onClose={() => {}}
        onUpdate={() => {}}
      />,
    );

    const statusGrid = screen.getByText('Current Status').closest('.player-status-card');
    expect(statusGrid).toBeTruthy();
    const turnsValue = within(statusGrid as HTMLElement).getByText('3');
    expect(turnsValue.className).toMatch(/low/);
  });

  it('invokes onClose from the footer Close button', () => {
    const onClose = vi.fn();

    render(
      <EmergencyOperationsPanel
        player={makePlayer()}
        onClose={onClose}
        onUpdate={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('invokes onClose from the header × button', () => {
    const onClose = vi.fn();

    render(
      <EmergencyOperationsPanel
        player={makePlayer()}
        onClose={onClose}
        onUpdate={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '×' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
