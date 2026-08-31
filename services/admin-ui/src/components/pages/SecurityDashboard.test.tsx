import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
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

function mockSecurityGets() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (String(url).includes('/security/report')) {
      return { data: sampleReport };
    }
    if (String(url).includes('/security/alerts')) {
      return { data: { alerts: [], alert_count: 0, high_priority_count: 0 } };
    }
    return { data: {} };
  });
}

function renderDash() {
  return render(
    <MemoryRouter>
      <SecurityDashboard />
    </MemoryRouter>
  );
}

function httpErr(status: number, detail?: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });
}

describe('SecurityDashboard cleanup + player action (LEG-1713)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    mockSecurityGets();
  });

  it('exposes cleanup and player-action controls', async () => {
    renderDash();
    expect(await screen.findByLabelText('Clean up old security data')).toBeTruthy();
    expect(screen.getByLabelText('Take player security action')).toBeTruthy();
  });

  it('posts tip security/cleanup with days_to_keep and toasts message', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { message: 'Cleaned up security data older than 7 days', days_kept: 7 },
    });
    renderDash();

    fireEvent.click(await screen.findByLabelText('Clean up old security data'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/security/cleanup',
        null,
        expect.objectContaining({ params: { days_to_keep: 7 } }),
      );
    });
    expect(toastSuccess).toHaveBeenCalledWith('Cleaned up security data older than 7 days');
  });

  it('cleanup 403 surfaces SECURITY_ACT scope copy', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403, data: {} } });
    renderDash();

    fireEvent.click(await screen.findByLabelText('Clean up old security data'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/SECURITY_ACT|Access denied/i);
  });

  it('cleanup 429 surfaces admin rate-limit helper copy', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 429, data: {} } });
    renderDash();

    fireEvent.click(await screen.findByLabelText('Clean up old security data'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });

  it('surfaces honest fallback on cleanup POST TypeError/network collapse (LEG-3030)', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));
    renderDash();

    fireEvent.click(await screen.findByLabelText('Clean up old security data'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        expect.stringMatching(/Failed to clean up security data/i),
      );
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('skips cleanup POST when operator cancels confirm', async () => {
    confirmMock.mockResolvedValue(false);
    renderDash();

    fireEvent.click(await screen.findByLabelText('Clean up old security data'));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
    });
    expect(api.post).not.toHaveBeenCalled();
  });

  it('posts tip player/{id}/action with block payload', async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { message: 'Player blocked' } });
    renderDash();

    fireEvent.change(await screen.findByLabelText('Player id for security action'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '24' },
    });
    fireEvent.click(screen.getByLabelText('Take player security action'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/security/player/player-uuid-1/action',
        { action: 'block', duration_hours: 24 },
      );
    });
    expect(toastSuccess).toHaveBeenCalledWith('Player blocked');
  });

  it('player action 403 surfaces SECURITY_ACT scope copy', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403, data: {} } });
    renderDash();

    fireEvent.change(await screen.findByLabelText('Player id for security action'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByLabelText('Take player security action'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/SECURITY_ACT|Access denied/i);
  });

  it('player action 429 surfaces admin rate-limit helper copy', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 429, data: {} } });
    renderDash();

    fireEvent.change(await screen.findByLabelText('Player id for security action'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByLabelText('Take player security action'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });
});

describe('SecurityDashboard overview load errors (LEG-2682)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces security report 403 as scope denial in overview alert', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        throw httpErr(403);
      }
      if (String(url).includes('/security/alerts')) {
        return { data: { alerts: [], alert_count: 0, high_priority_count: 0 } };
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Access denied/i);
    });
    expect(screen.getByRole('alert').textContent).toMatch(/security report scope/i);
    expect(screen.getByRole('alert').textContent).not.toMatch(/HTTP 403/i);
  });

  it('surfaces security alerts 403 as scope denial in overview alert', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        return { data: sampleReport };
      }
      if (String(url).includes('/security/alerts')) {
        throw httpErr(403);
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Access denied/i);
    });
    expect(screen.getByRole('alert').textContent).toMatch(/security alerts scope/i);
    expect(screen.getByRole('alert').textContent).not.toMatch(/HTTP 403/i);
  });

  it('surfaces security report 429 as admin rate-limit copy in overview alert', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        throw httpErr(429);
      }
      if (String(url).includes('/security/alerts')) {
        return { data: { alerts: [], alert_count: 0, high_priority_count: 0 } };
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.getByRole('alert').textContent).toMatch(/security report/i);
  });

  it('surfaces security alerts 429 as admin rate-limit copy in overview alert', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/security/report')) {
        return { data: sampleReport };
      }
      if (String(url).includes('/security/alerts')) {
        throw httpErr(429);
      }
      return { data: {} };
    });

    renderDash();

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
    expect(screen.getByRole('alert').textContent).toMatch(/security alerts/i);
  });
});

describe('SecurityDashboard axios Network Error densify (LEG-3393)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios-shaped Network Error on security report load', async () => {
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
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/security report:\s*unavailable/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses axios-shaped Network Error on security alerts load', async () => {
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
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/security alerts:\s*unavailable/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
  });

  it('collapses axios-shaped Network Error on cleanup POST', async () => {
    mockSecurityGets();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));
    renderDash();

    fireEvent.click(await screen.findByLabelText('Clean up old security data'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        expect.stringMatching(/Failed to clean up security data/i),
      );
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
  });
});

