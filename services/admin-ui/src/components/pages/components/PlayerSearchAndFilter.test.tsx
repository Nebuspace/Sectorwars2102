import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PlayerSearchAndFilter from './PlayerSearchAndFilter';
import type { PlayerFilters } from '../../../types/playerManagement';

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

describe('PlayerSearchAndFilter filter state (LEG-3128)', () => {
  it('renders default controls with no active-filter chrome', () => {
    const onFiltersChange = vi.fn();

    render(
      <PlayerSearchAndFilter
        filters={emptyFilters()}
        onFiltersChange={onFiltersChange}
      />,
    );

    expect(
      screen.getByPlaceholderText(/Search players by username, email, or ID/i),
    ).toBeTruthy();
    expect(screen.getByDisplayValue('All Status')).toBeTruthy();
    expect(screen.queryByText(/filters active/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /Clear All/i })).toBeNull();
  });

  it('propagates search and status filter changes', () => {
    const onFiltersChange = vi.fn();
    const filters = emptyFilters();

    render(
      <PlayerSearchAndFilter
        filters={filters}
        onFiltersChange={onFiltersChange}
      />,
    );

    fireEvent.change(
      screen.getByPlaceholderText(/Search players by username, email, or ID/i),
      { target: { value: 'nova' } },
    );
    expect(onFiltersChange).toHaveBeenCalledWith({
      ...filters,
      search: 'nova',
    });

    fireEvent.change(screen.getByDisplayValue('All Status'), {
      target: { value: 'banned' },
    });
    expect(onFiltersChange).toHaveBeenLastCalledWith({
      ...filters,
      status: 'banned',
    });
  });

  it('toggles quick filters and shows active-filter indicator', async () => {
    const user = userEvent.setup();
    const onFiltersChange = vi.fn();

    const { rerender } = render(
      <PlayerSearchAndFilter
        filters={emptyFilters()}
        onFiltersChange={onFiltersChange}
      />,
    );

    await user.click(screen.getByLabelText('Online Only'));
    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ onlineOnly: true }),
    );

    const activeFilters = {
      ...emptyFilters(),
      search: 'pilot',
      onlineOnly: true,
    };

    rerender(
      <PlayerSearchAndFilter
        filters={activeFilters}
        onFiltersChange={onFiltersChange}
      />,
    );

    expect(screen.getByText(/filters active/i)).toBeTruthy();
    expect(screen.getByRole('button', { name: /Clear All/i })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Clear All/i }));
    expect(onFiltersChange).toHaveBeenCalledWith(emptyFilters());

    fireEvent.click(screen.getByRole('button', { name: /Show Advanced/i }));
    expect(screen.getByText('Credit Range')).toBeTruthy();
    expect(screen.getByPlaceholderText('Min credits')).toBeTruthy();
  });
});
