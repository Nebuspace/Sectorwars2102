import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import GenerationLogPanel, {
  formatGenerationLogStreamError,
} from './GenerationLogPanel';
import type { BangJobStatus } from './types';

const mockStream = vi.fn();

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { error?: string }) =>
      opts?.error ? `${key}:${opts.error}` : key,
  }),
}));

vi.mock('../../../hooks/useBangGenerationStream', () => ({
  useBangGenerationStream: (jobId: string | null) => mockStream(jobId),
}));

function stubStream(overrides: {
  lines?: string[];
  status?: BangJobStatus;
  isStreaming?: boolean;
  error?: string | null;
} = {}) {
  return {
    lines: [],
    status: 'PENDING' as BangJobStatus,
    isStreaming: false,
    error: null,
    ...overrides,
  };
}

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

/**
 * LEG-3767 Soft-ORDER invent=0 — GenerationLogPanel stream TypeError/Network Error densify.
 */
describe('GenerationLogPanel typeErrorHonesty densify (LEG-3767)', () => {
  beforeEach(() => {
    mockStream.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('formatGenerationLogStreamError collapses Network Error to admin-safe fallback', () => {
    expect(formatGenerationLogStreamError('Network Error')).toMatch(
      /Generation log stream unavailable/i,
    );
  });

  it('formatGenerationLogStreamError collapses Failed to fetch to admin-safe fallback', () => {
    expect(formatGenerationLogStreamError('Failed to fetch')).toMatch(
      /Generation log stream unavailable/i,
    );
  });

  it('formatGenerationLogStreamError preserves hook SSE closed message', () => {
    expect(formatGenerationLogStreamError('SSE stream closed unexpectedly')).toBe(
      'SSE stream closed unexpectedly',
    );
  });

  it('collapses stream hook Network Error without leaking raw transport text', () => {
    mockStream.mockReturnValue(
      stubStream({
        error: 'Network Error',
        status: 'FAILED',
      }),
    );

    render(<GenerationLogPanel jobId="job-1" />);

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Generation log stream unavailable/i);
    assertNoTransportLeak(alert);
  });

  it('collapses stream hook Failed to fetch without leaking transport text', () => {
    mockStream.mockReturnValue(
      stubStream({
        error: 'Failed to fetch',
        status: 'FAILED',
      }),
    );

    render(<GenerationLogPanel jobId="job-1" />);

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Generation log stream unavailable/i);
    assertNoTransportLeak(alert);
  });
});
