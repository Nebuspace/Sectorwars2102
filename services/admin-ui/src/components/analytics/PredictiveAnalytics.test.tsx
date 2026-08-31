import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PredictiveAnalytics } from './PredictiveAnalytics';

describe('PredictiveAnalytics honesty note (LEG-3197)', () => {
  it('shows heading and note citing missing predictions endpoint', () => {
    render(<PredictiveAnalytics />);

    expect(screen.getByRole('heading', { name: /Predictive Analytics — unavailable/i })).toBeTruthy();

    const note = screen.getByRole('note');
    expect(note.textContent).toMatch(/\/api\/v1\/admin\/analytics\/predictions/);
    expect(note.textContent).toMatch(/not implemented/i);
  });

  it('does not render chart or timeframe controls', () => {
    render(<PredictiveAnalytics />);

    expect(screen.queryByRole('combobox')).toBeNull();
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.queryByRole('img')).toBeNull();
    expect(screen.queryByTestId('line-chart')).toBeNull();
    expect(screen.queryByTestId('doughnut-chart')).toBeNull();
    expect(screen.queryByLabelText(/timeframe/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /timeframe/i })).toBeNull();
  });
});
