import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import TeamManagement from './TeamManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useTeamUpdates: () => undefined,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
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
 * LEG-3417 Soft-ORDER — TeamManagement TypeError/Network Error honesty densify.
 * LEG-3924 Soft-ORDER — 403/429 HTTP honesty densify.
 * formatAdminApiError collapses transport failures to team-load fallback.
 */
describe('TeamManagement typeErrorHonesty densify (LEG-3417)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error to team load fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load team data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load team data/i).textContent ?? '';
    expect(text).toMatch(/Failed to load team data/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch to team load fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load team data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load team data/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with team management scope copy when teams GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Access denied|team management scopes/i)).toBeTruthy();
    });
    const text = screen.getByText(/Access denied|team management scopes/i).textContent ?? '';
    expect(text).toMatch(/Access denied|team management scopes/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on teams GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<TeamManagement />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

});
