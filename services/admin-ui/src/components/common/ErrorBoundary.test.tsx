import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ErrorBoundary from './ErrorBoundary';

function ThrowOnRender({ message }: { message: string }): never {
  throw new Error(message);
}

describe('ErrorBoundary crash fallback (LEG-3178)', () => {
  const reload = vi.fn();
  const originalLocation = window.location;

  beforeEach(() => {
    reload.mockReset();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...originalLocation, reload },
    });
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: originalLocation,
    });
    vi.restoreAllMocks();
  });

  it('renders healthy children when no error', () => {
    render(
      <ErrorBoundary>
        <div>healthy child</div>
      </ErrorBoundary>,
    );

    expect(screen.getByText('healthy child')).toBeTruthy();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows fallback panel when a child throws on render', () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender message="render boom" />
      </ErrorBoundary>,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toBeTruthy();
    expect(screen.getByText('This page crashed')).toBeTruthy();
    expect(screen.getByText(/Error: render boom/)).toBeTruthy();
  });

  it('calls window.location.reload when Reload Page is clicked', () => {
    render(
      <ErrorBoundary>
        <ThrowOnRender message="reload test" />
      </ErrorBoundary>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Reload Page' }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
