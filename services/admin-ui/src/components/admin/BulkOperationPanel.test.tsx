import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import BulkOperationPanel from './BulkOperationPanel';
import type { PlayerModel } from '../../types/playerManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    post: vi.fn(),
  },
}));

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

describe('BulkOperationPanel (LEG-904)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
  });

  it('shows operation cards and requires reason before Execute enables', () => {
    render(
      <BulkOperationPanel
        selectedPlayers={[makePlayer('p1', 'Alpha')]}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );

    expect(screen.getByText('Adjust Credits')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Execute' })).toHaveProperty('disabled', true);

    fireEvent.click(screen.getByText('Adjust Credits'));
    fireEvent.change(screen.getByLabelText('Credit delta'), { target: { value: '50' } });
    expect(screen.getByRole('button', { name: 'Execute' })).toHaveProperty('disabled', true);

    fireEvent.change(screen.getByLabelText('Reason (required, audit-visible)'), {
      target: { value: 'compensation event' },
    });
    expect(screen.getByRole('button', { name: 'Execute' })).toHaveProperty('disabled', false);
  });

  it('posts CREDIT_ADJUST payload after confirmation and renders results', async () => {
    const onComplete = vi.fn();
    vi.mocked(api.post).mockResolvedValue({
      data: {
        operation: 'CREDIT_ADJUST',
        applied: 2,
        rejected: 0,
        results: [
          { player_id: 'p1', success: true },
          { player_id: 'p2', success: true },
        ],
      },
    });

    render(
      <BulkOperationPanel
        selectedPlayers={[makePlayer('p1', 'Alpha'), makePlayer('p2', 'Bravo')]}
        onClose={() => {}}
        onComplete={onComplete}
      />,
    );

    fireEvent.click(screen.getByText('Adjust Credits'));
    fireEvent.change(screen.getByLabelText('Credit delta'), { target: { value: '100' } });
    fireEvent.change(screen.getByLabelText('Reason (required, audit-visible)'), {
      target: { value: 'event grant' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Execute' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Execute' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/players/bulk-operation', {
        player_ids: ['p1', 'p2'],
        operation: 'CREDIT_ADJUST',
        parameters: { reason: 'event grant', amount: 100 },
      });
    });

    expect(screen.getByText('Bulk Operation Results')).toBeTruthy();
    expect(screen.getByText('2')).toBeTruthy();
    expect(screen.getByText('Alpha')).toBeTruthy();
    expect(onComplete).toHaveBeenCalledWith('CREDIT_ADJUST', expect.any(Object));
  });

  it('surfaces backend validation errors via formatAdminApiError', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: { detail: 'Missing required scope: admin.players.adjust_credits' } },
    });

    render(
      <BulkOperationPanel
        selectedPlayers={[makePlayer('p1', 'Alpha')]}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );

    fireEvent.click(screen.getByText('Adjust Credits'));
    fireEvent.change(screen.getByLabelText('Credit delta'), { target: { value: '10' } });
    fireEvent.change(screen.getByLabelText('Reason (required, audit-visible)'), {
      target: { value: 'test' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Execute' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm Execute' }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Missing required scope/);
    });
  });

  it('lists selected players with overflow truncation at 10', () => {
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
    expect(screen.getByText('Player10')).toBeTruthy();
    expect(screen.queryByText('Player11')).toBeNull();
    expect(screen.getByText('...and 2 more players')).toBeTruthy();
  });

  it('invokes onClose from footer Close button', () => {
    const onClose = vi.fn();
    render(
      <BulkOperationPanel selectedPlayers={[]} onClose={onClose} onComplete={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
