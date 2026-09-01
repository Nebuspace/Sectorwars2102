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
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/risk')) {
        return { data: sampleRisk };
      }
      if (String(url).includes('/status')) {
        return { data: sampleStatus };
      }
      return { data: {} };
    });

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
    });

    expect(await screen.findByLabelText('Player risk assessment')).toBeTruthy();
    expect(screen.getByLabelText('Player security status')).toBeTruthy();
    expect(screen.getByText('high (45)')).toBeTruthy();
    expect(screen.getByText('Low trust score')).toBeTruthy();
  });

  it('confirms then posts block action and reloads assessment', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (String(url).includes('/risk')) {
        return { data: sampleRisk };
      }
      if (String(url).includes('/status')) {
        return { data: sampleStatus };
      }
      return { data: {} };
    });
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
    expect(api.get).toHaveBeenCalledTimes(2);
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
