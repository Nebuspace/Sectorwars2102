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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toMatch(/Network Error/i);
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3757 Soft-ORDER invent=0 — PlayerSearchAndFilter TypeError/Network Error densify.
 * LEG-3929 Soft-ORDER — 403/429 HTTP honesty densify.
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

  it('does not surface raw 403 transport when api mocks reject with HTTP 403 (invent=0 filter shell)', () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));
    vi.mocked(api.post).mockRejectedValue(axiosError(403));
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
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('does not surface raw 429 transport when api mocks reject with HTTP 429 (invent=0 filter shell)', () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));
    vi.mocked(api.post).mockRejectedValue(axiosError(429));
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
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

});
