import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import PlayerSearchAndFilter from './PlayerSearchAndFilter';
import type { PlayerFilters } from '../../../types/playerManagement';
import { api } from '../../../utils/auth';

vi.mock('../../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const emptyFilters = (): PlayerFilters => ({
  search: '',
  status: 'all',
  team: null,
  minCredits: null,
  maxCredits: null,
  lastLoginAfter: null,
  lastLoginBefore: null,
  reputationFilter: null,
  hasShips: null,
  hasPlanets: null,
  hasPorts: null,
  onlineOnly: false,
  suspiciousActivity: false,
});

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
 * LEG-3757 Soft-ORDER invent=0 — PlayerSearchAndFilter TypeError/Network Error densify.
 * Filter shell only: parent PlayerAnalytics owns search fetch; no transport on this surface.
 */
describe('PlayerSearchAndFilter typeErrorHonesty densify (LEG-3757)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    rejectAllApiMocks();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('does not issue player-search API calls on mount (invent=0)', () => {
    const onFiltersChange = vi.fn();

    render(
      <PlayerSearchAndFilter
        filters={emptyFilters()}
        onFiltersChange={onFiltersChange}
      />,
    );

    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
    assertNoTransportLeak(document.body.textContent ?? '');
  });

  it('does not issue player-search API calls on filter interactions (invent=0)', () => {
    const onFiltersChange = vi.fn();
    const activeFilters = { ...emptyFilters(), search: 'pilot', onlineOnly: true };

    const { rerender } = render(
      <PlayerSearchAndFilter
        filters={emptyFilters()}
        onFiltersChange={onFiltersChange}
      />,
    );

    fireEvent.change(
      screen.getByPlaceholderText(/Search players by username, email, or ID/i),
      { target: { value: 'nova' } },
    );
    fireEvent.click(screen.getByLabelText('Online Only'));

    rerender(
      <PlayerSearchAndFilter
        filters={activeFilters}
        onFiltersChange={onFiltersChange}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Clear All/i }));
    fireEvent.click(screen.getByRole('button', { name: /Show Advanced/i }));

    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).toBeNull();
    assertNoTransportLeak(document.body.textContent ?? '');
  });
});
