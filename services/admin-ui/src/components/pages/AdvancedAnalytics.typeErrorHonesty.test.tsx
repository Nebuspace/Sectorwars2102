import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AdvancedAnalytics } from './AdvancedAnalytics';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../analytics/CustomReportBuilder', () => ({
  CustomReportBuilder: ({
    onGenerate,
  }: {
    onGenerate: (template: { name: string }) => void;
  }) => (
    <button
      type="button"
      data-testid="trigger-generate-report"
      onClick={() => onGenerate({ name: 'Test Report' })}
    >
      Generate report
    </button>
  ),
}));

vi.mock('../analytics/PredictiveAnalytics', () => ({
  PredictiveAnalytics: () => <div data-testid="predictive-stub" />,
}));

vi.mock('../analytics/PerformanceMetrics', () => ({
  PerformanceMetrics: () => <div data-testid="performance-stub" />,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

/**
 * LEG-3661 Soft-ORDER — AdvancedAnalytics TypeError/Network Error densify.
 * LEG-3868 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('AdvancedAnalytics typeErrorHonesty densify (LEG-3661)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('collapses axios Network Error on report generate without leaking raw transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByTestId('trigger-generate-report'));

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });
    const text = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(text).toMatch(/Failed to generate report/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on report generate without leaking transport text', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByTestId('trigger-generate-report'));

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });
    const text = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(text).toMatch(/Failed to generate report/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on analytics export without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));
    const exportButtons = screen.getAllByRole('button', { name: /^Export$/i });
    fireEvent.click(exportButtons[0]);

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });
    const text = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(text).toMatch(/Export failed/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on analytics export without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));
    const exportButtons = screen.getAllByRole('button', { name: /^Export$/i });
    fireEvent.click(exportButtons[0]);

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });
    const text = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(text).toMatch(/Export failed/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with friendly scope copy when analytics export GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));
    const exportButtons = screen.getAllByRole('button', { name: /^Export$/i });
    fireEvent.click(exportButtons[0]);

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });
    const text = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(text).toMatch(/Access denied|audit\.view/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on analytics export GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<AdvancedAnalytics />);
    fireEvent.click(screen.getByRole('button', { name: /Data Export/i }));
    const exportButtons = screen.getAllByRole('button', { name: /^Export$/i });
    fireEvent.click(exportButtons[0]);

    await waitFor(() => {
      expect(screen.getByTestId('analytics-save-message')).toBeTruthy();
    });
    const text = screen.getByTestId('analytics-save-message').textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
