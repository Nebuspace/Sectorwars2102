import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import ErrorBoundary from './ErrorBoundary';

function ThrowingChild({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error('Test render crash');
  }
  return <div>Healthy child content</div>;
}

describe('ErrorBoundary crash fallback (LEG-3167)', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders healthy children when no error occurs', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow={false} />
      </ErrorBoundary>,
    );

    expect(screen.getByText('Healthy child content')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows alert fallback with error name and message when a child throws', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow />
      </ErrorBoundary>,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toBeTruthy();
    expect(screen.getByText(/This page crashed/i)).toBeTruthy();
    expect(screen.getByText(/Error: Test render crash/)).toBeTruthy();
  });

  it('shows a reload button in the crash fallback', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('button', { name: /Reload Page/i })).toBeTruthy();
  });
});
