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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

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
    },
  } as PlayerModel;
}

async function submitCreditAdjust() {
  fireEvent.click(screen.getByText('Adjust Credits'));
  fireEvent.change(screen.getByLabelText('Credit delta'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('Reason (required, audit-visible)'), {
    target: { value: 'test densify' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Execute' }));
  fireEvent.click(screen.getByRole('button', { name: 'Confirm Execute' }));
}

/**
 * LEG-3454 Soft-ORDER — BulkOperationPanel TypeError/Network Error honesty densify.
 */
describe('BulkOperationPanel typeErrorHonesty densify (LEG-3454)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on submit to bulk-op fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(
      <BulkOperationPanel
        selectedPlayers={[makePlayer('p1', 'Alpha')]}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );
    await submitCreditAdjust();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Bulk operation failed/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on submit to bulk-op fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <BulkOperationPanel
        selectedPlayers={[makePlayer('p1', 'Alpha')]}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );
    await submitCreditAdjust();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Bulk operation failed/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with bulk-op scope hint when POST is denied', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    render(
      <BulkOperationPanel
        selectedPlayers={[makePlayer('p1', 'Alpha')]}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );
    await submitCreditAdjust();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|bulk player operations|ADJUST_CREDITS/i);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on bulk POST', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    render(
      <BulkOperationPanel
        selectedPlayers={[makePlayer('p1', 'Alpha')]}
        onClose={() => {}}
        onComplete={() => {}}
      />,
    );
    await submitCreditAdjust();

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });
});
