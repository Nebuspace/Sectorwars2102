import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SecurityDashboard } from './SecurityDashboard';
import { api } from '../../utils/auth';

const toastSuccess = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn(async () => true);

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { mfaEnabled: true }, logout: vi.fn() }),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => confirmMock,
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useSystemAlerts: () => undefined,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../security/AuditLogViewer', () => ({
  AuditLogViewer: () => <div data-testid="audit-stub" />,
}));

vi.mock('../auth/MFASetup', () => ({
  MFASetup: () => <div data-testid="mfa-stub" />,
}));

const sampleReport = {
  timestamp: '2026-08-22T00:00:00Z',
  players: { total: 10, blocked: 1, high_risk: 2, blocked_percentage: 10 },
  violations: { total: 3, by_type: {}, average_per_player: 0.3 },
  costs: {
    total_today_usd: 0.01,
    average_per_player_usd: 0.001,
    highest_spender: null,
    players_over_limit: 0,
  },
  rate_limits: {
    requests_per_minute: 60,
    requests_per_hour: 600,
    requests_per_day: 6000,
    max_cost_per_day_usd: 1,
  },
};

function renderDash() {
  return render(
    <MemoryRouter>
      <SecurityDashboard />
    </MemoryRouter>,
  );
}

/**
 * LEG-3636 Soft-ORDER — SecurityDashboard TypeError/Network Error densify.
 */
describe('SecurityDashboard typeErrorHonesty densify (LEG-3636)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on security report overview load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        throw new Error('Network Error');
      }
      if (String(url).includes('/security/alerts')) {
        return { data: { alerts: [], alert_count: 0, high_priority_count: 0 } };
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/security report/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/unavailable/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses axios Network Error on security alerts overview load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        return { data: sampleReport };
      }
      if (String(url).includes('/security/alerts')) {
        throw new Error('Network Error');
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/security alerts/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/unavailable/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on security report overview load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        throw new TypeError('Failed to fetch');
      }
      if (String(url).includes('/security/alerts')) {
        return { data: { alerts: [], alert_count: 0, high_priority_count: 0 } };
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/security report/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/unavailable/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on security alerts overview load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        return { data: sampleReport };
      }
      if (String(url).includes('/security/alerts')) {
        throw new TypeError('Failed to fetch');
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/security alerts/i);
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/unavailable/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
