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

/**
 * LEG-3417 Soft-ORDER — TeamManagement TypeError/Network Error honesty densify.
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
});
