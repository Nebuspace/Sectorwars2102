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
    response: { status, data: detail !== undefined ? { detail } : {} },
  });

describe('DisputePanel (LEG-1099 scope errors)', () => {
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

  it('shows scope-aware copy on 403 resolve when GS sends no detail', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));
    await openResolveForm();

    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/COMBAT_INTERVENE|combat intervene|Access denied/i);
  });

  it('surfaces GS string detail on 403 when present (formatAdminApiError contract)', async () => {
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing combat.intervene scope')
    );
    await openResolveForm();

    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Missing combat.intervene scope');
    });
  });

  it('shows admin rate-limit copy on 429 resolve', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));
    await openResolveForm();

    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
  });

  it('surfaces honest fallback on non-RBAC network collapse (LEG-2961)', async () => {
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
});
