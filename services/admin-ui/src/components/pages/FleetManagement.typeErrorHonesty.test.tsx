import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import FleetManagement from './FleetManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useFleetUpdates: () => undefined,
}));

vi.mock('../charts/FleetHealthReport', () => ({
  default: () => <div data-testid="fleet-health-stub" />,
}));

vi.mock('../fleet/FleetOperationsTab', () => ({
  default: () => <div data-testid="fleet-ops-stub" />,
}));

/**
 * LEG-3437 Soft-ORDER — FleetManagement TypeError/Network Error honesty densify.
 */
describe('FleetManagement typeErrorHonesty densify (LEG-3437)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on load to fleet data fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<FleetManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch fleet data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to fetch fleet data/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on load to fleet data fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FleetManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch fleet data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to fetch fleet data/i).textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toBe('Failed to fetch');
  });
});
