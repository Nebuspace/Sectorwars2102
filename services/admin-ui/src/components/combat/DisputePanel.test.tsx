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

const axiosError = (status: number, detail = 'raw_scope_detail_should_not_surface') =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: { detail } },
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

  it('shows scope-aware copy on 403 resolve, never raw axios detail', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));
    await openResolveForm();

    fireEvent.click(screen.getByRole('button', { name: /Resolve Dispute/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/COMBAT_INTERVENE|combat intervene|Access denied/i);
    expect(alert).not.toContain('raw_scope_detail_should_not_surface');
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
    expect(alert).not.toContain('raw_scope_detail_should_not_surface');
  });
});
