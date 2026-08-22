import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import DroneOperationsTab from './DroneOperationsTab';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }),
  useConfirm: () => vi.fn(),
}));

describe('DroneOperationsTab scope errors', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
  });

  it('reports all-reject 403 as PLAYERS_VIEW, not generic Failed', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 403 } });
    render(<DroneOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/PLAYERS_VIEW/);
    });
    expect(document.body.textContent).not.toContain('Failed to load drone operations data.');
  });

  it('reports all-reject 429 as admin rate-limit', async () => {
    vi.mocked(api.get).mockRejectedValue({ response: { status: 429 } });
    render(<DroneOperationsTab />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/rate limit/i);
    });
  });
});
