import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationFilters } from './ConversationFilters';
import type { ConversationFilters as Filters } from '../../types/firstLogin';

const baseFilters = (): Filters => ({
  limit: 20,
  skip: 5,
});

describe('ConversationFilters controls (LEG-3168)', () => {
  it('calls onFilterChange with skip reset when outcome changes', () => {
    const onFilterChange = vi.fn();
    const filters = baseFilters();

    render(
      <ConversationFilters
        filters={filters}
        onFilterChange={onFilterChange}
        onRefresh={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText('Outcome'), {
      target: { value: 'SUCCESS' },
    });

    expect(onFilterChange).toHaveBeenCalledWith({
      ...filters,
      outcome: 'SUCCESS',
      skip: 0,
    });
  });

  it('clear filters resets active fields while preserving limit', () => {
    const onFilterChange = vi.fn();
    const filters: Filters = {
      limit: 25,
      skip: 3,
      outcome: 'CAUGHT',
      ai_provider: 'openai',
      start_date: '2026-01-01',
      end_date: '2026-01-31',
    };

    render(
      <ConversationFilters
        filters={filters}
        onFilterChange={onFilterChange}
        onRefresh={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Clear Filters/i }));

    expect(onFilterChange).toHaveBeenCalledWith({
      limit: 25,
      skip: 0,
    });
  });

  it('invokes onRefresh when refresh is clicked', () => {
    const onRefresh = vi.fn();

    render(
      <ConversationFilters
        filters={baseFilters()}
        onFilterChange={vi.fn()}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Refresh/i }));

    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('disables refresh while loading', () => {
    render(
      <ConversationFilters
        filters={baseFilters()}
        onFilterChange={vi.fn()}
        onRefresh={vi.fn()}
        loading
      />,
    );

    expect(screen.getByRole('button', { name: /Refresh/i })).toBeDisabled();
  });
});
