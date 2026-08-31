import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ConversationFilters } from './ConversationFilters';
import type { ConversationFilters as Filters } from '../../types/firstLogin';

const baseFilters: Filters = { limit: 25, skip: 10 };

describe('ConversationFilters filter and refresh (LEG-3179)', () => {
  const onFilterChange = vi.fn();
  const onRefresh = vi.fn();

  beforeEach(() => {
    onFilterChange.mockReset();
    onRefresh.mockReset();
  });

  it('emits outcome filter with skip reset to 0', () => {
    render(
      <ConversationFilters
        filters={baseFilters}
        onFilterChange={onFilterChange}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.change(screen.getByLabelText('Outcome'), {
      target: { value: 'SUCCESS' },
    });

    expect(onFilterChange).toHaveBeenCalledWith({
      limit: 25,
      skip: 0,
      outcome: 'SUCCESS',
    });
  });

  it('emits provider and date filters with skip reset to 0', () => {
    render(
      <ConversationFilters
        filters={baseFilters}
        onFilterChange={onFilterChange}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.change(screen.getByLabelText('AI Provider'), {
      target: { value: 'openai' },
    });
    fireEvent.change(screen.getByLabelText('Start Date'), {
      target: { value: '2026-01-01' },
    });
    fireEvent.change(screen.getByLabelText('End Date'), {
      target: { value: '2026-01-31' },
    });

    expect(onFilterChange).toHaveBeenNthCalledWith(1, {
      limit: 25,
      skip: 0,
      ai_provider: 'openai',
    });
    expect(onFilterChange).toHaveBeenNthCalledWith(2, {
      limit: 25,
      skip: 0,
      start_date: '2026-01-01',
    });
    expect(onFilterChange).toHaveBeenNthCalledWith(3, {
      limit: 25,
      skip: 0,
      end_date: '2026-01-31',
    });
  });

  it('shows Clear Filters only when filters are active and resets optional fields', () => {
    const activeFilters: Filters = {
      limit: 25,
      skip: 5,
      outcome: 'CAUGHT',
      ai_provider: 'anthropic',
    };

    const { rerender } = render(
      <ConversationFilters
        filters={baseFilters}
        onFilterChange={onFilterChange}
        onRefresh={onRefresh}
      />,
    );

    expect(screen.queryByRole('button', { name: /Clear Filters/i })).toBeNull();

    rerender(
      <ConversationFilters
        filters={activeFilters}
        onFilterChange={onFilterChange}
        onRefresh={onRefresh}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /Clear Filters/i }));
    expect(onFilterChange).toHaveBeenCalledWith({ limit: 25, skip: 0 });
  });

  it('calls onRefresh and disables the button while loading', () => {
    const { rerender } = render(
      <ConversationFilters
        filters={baseFilters}
        onFilterChange={onFilterChange}
        onRefresh={onRefresh}
      />,
    );

    const refresh = screen.getByRole('button', { name: /Refresh/i });
    fireEvent.click(refresh);
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(refresh).not.toBeDisabled();

    rerender(
      <ConversationFilters
        filters={baseFilters}
        onFilterChange={onFilterChange}
        onRefresh={onRefresh}
        loading
      />,
    );

    expect(screen.getByRole('button', { name: /Refresh/i })).toBeDisabled();
  });
});
