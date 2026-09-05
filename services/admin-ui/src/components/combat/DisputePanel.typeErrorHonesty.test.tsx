import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { DisputePanel } from './DisputePanel';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    post: vi.fn(),
  },
}));

const sampleDispute = {
  id: 'disp-1',
  combat_id: 'combat-99',
  type: 'exploit',
  severity: 'high',
  timestamp: '2026-08-20T00:00:00Z',
  description: 'Suspected combat exploit',
  participants: {},
  status: 'pending',
  recommended_action: 'investigate',
};


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
 * LEG-3431 Soft-ORDER — DisputePanel TypeError/Network Error honesty densify.
 * LEG-3890 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('DisputePanel typeErrorHonesty densify (LEG-3431)', () => {
  beforeEach(() => {
    vi.mocked(api.post).mockReset();
  });

  async function openResolveForm() {
    render(<DisputePanel disputes={[sampleDispute]} />);
    fireEvent.click(screen.getByText(/Suspected combat exploit/i));
    const select = screen.getByDisplayValue('Select action...');
    fireEvent.change(select, { target: { value: 'No violation found' } });
    const notes = screen.getByPlaceholderText(/Add detailed notes/i);
    fireEvent.change(notes, { target: { value: 'Investigated — clean fight.' } });
  }

  it('collapses axios Network Error on resolve to gameserver-unreachable fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));
    await openResolveForm();

    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error resolving dispute/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on resolve to gameserver-unreachable fallback', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));
    await openResolveForm();

    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Gameserver unreachable|network error resolving dispute/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toBe('Failed to fetch');
  });

  it('surfaces 403 with COMBAT_INTERVENE scope copy when resolve POST is denied', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));
    await openResolveForm();
    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Access denied|COMBAT_INTERVENE/i);
    expect(alert).not.toMatch(/\b403\b/);
    expect(alert).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(alert);
  });

  it('surfaces 429 as admin rate-limit copy on resolve POST', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));
    await openResolveForm();
    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
    expect(alert).not.toMatch(/\b429\b/);
    expect(alert).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(alert);
  });

});
