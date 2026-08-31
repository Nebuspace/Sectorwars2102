import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import GenerationLogPanel from './GenerationLogPanel';
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

describe('GenerationLogPanel (LEG-3170)', () => {
  beforeEach(() => {
    mockStream.mockReset();
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it('shows empty placeholder when jobId is null', () => {
    mockStream.mockReturnValue(stubStream());

    render(<GenerationLogPanel jobId={null} />);

    expect(screen.getByText('bang.log.empty')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'bang.log.copy' })).toBeDisabled();
  });

  it('renders classified log categories for sample lines', () => {
    mockStream.mockReturnValue(
      stubStream({
        lines: [
          'plain info line',
          'TOPOLOGY_RESCUE applied bridge edge',
          'EMISSION_UNDERTARGET sector 42',
        ],
        status: 'RUNNING',
        isStreaming: true,
      }),
    );

    render(<GenerationLogPanel jobId="job-1" />);

    expect(screen.getByText('TOPOLOGY_RESCUE')).toBeTruthy();
    expect(screen.getByText('EMISSION_UNDERTARGET')).toBeTruthy();
    expect(screen.getByText('plain info line')).toBeTruthy();
    expect(screen.getByText('bang.log.streaming')).toBeTruthy();
  });

  it('toggles auto-scroll via pause/resume control', () => {
    mockStream.mockReturnValue(
      stubStream({
        lines: ['line one'],
        status: 'RUNNING',
        isStreaming: true,
      }),
    );

    render(<GenerationLogPanel jobId="job-1" />);

    const pauseBtn = screen.getByRole('button', { name: 'bang.log.pause' });
    expect(pauseBtn).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(pauseBtn);

    const resumeBtn = screen.getByRole('button', { name: 'bang.log.resume' });
    expect(resumeBtn).toHaveAttribute('aria-pressed', 'true');
  });

  it('copies accumulated log lines to clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });

    mockStream.mockReturnValue(
      stubStream({
        lines: ['alpha', 'beta'],
        status: 'COMPLETE',
      }),
    );

    render(<GenerationLogPanel jobId="job-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'bang.log.copy' }));

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('alpha\nbeta');
    });
    expect(screen.getByRole('button', { name: 'bang.log.copied' })).toBeTruthy();
  });
});
