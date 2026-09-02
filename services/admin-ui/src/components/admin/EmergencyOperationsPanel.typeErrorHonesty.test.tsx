import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import EmergencyOperationsPanel from './EmergencyOperationsPanel';
import type { PlayerModel } from '../../types/playerManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

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

function rejectAllApiMocks() {
  vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
  vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));
}

function assertNoTransportLeak(text: string) {
  expect(text).not.toMatch(/Network Error/i);
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3754 Soft-ORDER invent=0 — EmergencyOperationsPanel TypeError/Network Error densify.
 * Panel is an honesty shell: no extended-player load or emergency-operation action APIs.
 */
describe('EmergencyOperationsPanel typeErrorHonesty densify (LEG-3754)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    rejectAllApiMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('does not issue emergency extended-player load API calls on mount (invent=0)', () => {
    render(
      <EmergencyOperationsPanel
        player={makePlayer()}
        onClose={() => {}}
        onUpdate={() => {}}
      />,
    );

    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
    assertNoTransportLeak(document.body.textContent ?? '');
  });

  it('does not issue emergency-operation action API calls on Close interaction (invent=0)', () => {
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
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
    assertNoTransportLeak(document.body.textContent ?? '');
  });

  it('surfaces honesty note citing missing endpoints instead of transport errors', () => {
    render(
      <EmergencyOperationsPanel
        player={makePlayer()}
        onClose={() => {}}
        onUpdate={() => {}}
      />,
    );

    const note = screen.getByRole('note');
    const text = note.textContent ?? '';
    expect(text).toMatch(/POST \/api\/v1\/admin\/players\/emergency-operation/);
    expect(text).toMatch(/GET \/api\/v1\/admin\/players\/\{id\}\/extended/);
    expect(text).toMatch(/not implemented/i);
    assertNoTransportLeak(text);
    expect(screen.queryByRole('button', { name: /Execute/i })).toBeNull();
  });
});
