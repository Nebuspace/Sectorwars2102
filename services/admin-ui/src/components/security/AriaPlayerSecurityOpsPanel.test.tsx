import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AriaPlayerSecurityOpsPanel } from './AriaPlayerSecurityOpsPanel';
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

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => confirmMock,
}));

const sampleRisk = {
  player_id: 'player-uuid-1',
  risk_level: 'high',
  risk_score: 45,
  risk_factors: ['Low trust score', 'Recent violations'],
  trust_score: 0.25,
  violation_count: 3,
  is_blocked: false,
  daily_cost_usd: 0.42,
  last_violation: '2026-08-30T12:00:00Z',
};

const sampleStatus = {
  is_blocked: false,
  trust_score: 0.25,
  violation_count: 3,
  last_violation: '2026-08-30T12:00:00Z',
  request_count_1min: 2,
  request_count_1day: 40,
  block_expires: null,
};

const emptyLogPage = {
  items: [] as Array<{
    id: string;
    timestamp: string;
    event_type: string;
    severity: string;
    description: string;
  }>,
  page: 1,
  limit: 20,
  total: 0,
  pages: 0,
};

function mockRiskStatusLogs(
  risk = sampleRisk,
  status = sampleStatus,
  logs = emptyLogPage,
) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (String(url).includes('/risk')) {
      return { data: risk };
    }
    if (String(url).includes('/status')) {
      return { data: status };
    }
    if (String(url).includes('/logs')) {
      return { data: logs };
    }
    return { data: {} };
  });
}

describe('AriaPlayerSecurityOpsPanel (LEG-272)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('loads risk and status when assessment is requested', async () => {
    mockRiskStatusLogs();

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        '/api/v1/admin/security/player/player-uuid-1/risk',
      );
      expect(api.get).toHaveBeenCalledWith(
        '/api/v1/admin/security/player/player-uuid-1/status',
      );
      expect(api.get).toHaveBeenCalledWith(
        '/api/v1/admin/security/player/player-uuid-1/logs?page=1&limit=20',
      );
    });

    expect(await screen.findByLabelText('Player risk assessment')).toBeTruthy();
    expect(screen.getByLabelText('Player security status')).toBeTruthy();
    expect(screen.getByText('high (45)')).toBeTruthy();
    expect(screen.getByText('Low trust score')).toBeTruthy();
  });

  it('confirms then posts block action and reloads assessment', async () => {
    mockRiskStatusLogs();
    vi.mocked(api.post).mockResolvedValue({
      data: { message: 'Player blocked for 24 hours' },
    });

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '24' },
    });
    fireEvent.click(screen.getByLabelText('Take ARIA player security action'));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/security/player/player-uuid-1/action',
        { action: 'block', duration_hours: 24 },
      );
    });

    expect(toastSuccess).toHaveBeenCalledWith('Player blocked for 24 hours');
    expect(api.get).toHaveBeenCalledTimes(3);
  });

  it('skips POST when operator cancels confirm', async () => {
    confirmMock.mockResolvedValue(false);
    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByLabelText('Take ARIA player security action'));

    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
    });
    expect(api.post).not.toHaveBeenCalled();
  });

  it('403 on risk load surfaces admin.aria.audit scope copy', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/risk')) {
        throw { response: { status: 403, data: {} } };
      }
      if (String(url).includes('/status')) {
        return { data: sampleStatus };
      }
      if (String(url).includes('/logs')) {
        return { data: emptyLogPage };
      }
      return { data: {} };
    });

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/admin\.aria\.audit/i);
    });
  });

  it('action 403 surfaces admin.aria.audit scope copy', async () => {
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403, data: {} } });
    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByLabelText('Take ARIA player security action'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/admin\.aria\.audit/i);
  });
});

/**
 * LEG-3544 Soft-ORDER — TypeError / axios Network Error densify (invent=0).
 * formatAdminApiError already on catch; assert raw transport never surfaces.
 */
describe('AriaPlayerSecurityOpsPanel Network Error densify (LEG-3544)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('collapses axios-shaped Network Error on risk/status load', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load player (risk assessment|security status)/i);
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on risk/status load', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });

    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load player (risk assessment|security status)/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error on action POST', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByLabelText('Take ARIA player security action'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });

    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/Failed to take player security action/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on action POST', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.change(screen.getByLabelText('Block duration in hours'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByLabelText('Take ARIA player security action'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });

    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/Failed to take player security action/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });
});

describe('AriaPlayerSecurityOpsPanel ARIASecurityLog history (LEG-3607)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('renders log rows after assessment load', async () => {
    mockRiskStatusLogs(sampleRisk, sampleStatus, {
      items: [
        {
          id: 'log-1',
          timestamp: '2026-08-30T10:00:00Z',
          event_type: 'invalid_market_observation',
          severity: 'warning',
          description: 'Player not docked at station',
        },
      ],
      page: 1,
      limit: 20,
      total: 1,
      pages: 1,
    });

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    expect(await screen.findByLabelText('ARIA security log history')).toBeTruthy();
    expect(screen.getByText('invalid_market_observation')).toBeTruthy();
    expect(screen.getByText('warning')).toBeTruthy();
    expect(screen.getByText('Player not docked at station')).toBeTruthy();
  });

  it('403 on log fetch surfaces admin.aria.audit scope copy', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/risk')) {
        return { data: sampleRisk };
      }
      if (String(url).includes('/status')) {
        return { data: sampleStatus };
      }
      if (String(url).includes('/logs')) {
        throw { response: { status: 403, data: {} } };
      }
      return { data: {} };
    });

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByLabelText('ARIA security log history')).toBeTruthy();
    });

    const logAlert = screen.getByLabelText('ARIA security log history').querySelector('[role="alert"]');
    expect(logAlert?.textContent ?? '').toMatch(/admin\.aria\.audit/i);
  });

  it('429 on log fetch surfaces rate-limit copy', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/risk')) {
        return { data: sampleRisk };
      }
      if (String(url).includes('/status')) {
        return { data: sampleStatus };
      }
      if (String(url).includes('/logs')) {
        throw { response: { status: 429, data: {} } };
      }
      return { data: {} };
    });

    render(<AriaPlayerSecurityOpsPanel />);

    fireEvent.change(screen.getByLabelText('Player id for ARIA security assessment'), {
      target: { value: 'player-uuid-1' },
    });
    fireEvent.click(screen.getByLabelText('Load ARIA security assessment'));

    await waitFor(() => {
      expect(screen.getByLabelText('ARIA security log history')).toBeTruthy();
    });

    const logAlert = screen.getByLabelText('ARIA security log history').querySelector('[role="alert"]');
    expect(logAlert?.textContent ?? '').toMatch(/rate limit/i);
  });
});
