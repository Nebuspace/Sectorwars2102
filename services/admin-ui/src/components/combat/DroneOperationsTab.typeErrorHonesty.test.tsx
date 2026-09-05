import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import DroneOperationsTab from './DroneOperationsTab';
import { api } from '../../utils/auth';

const toastSuccess = vi.fn();
const toastError = vi.fn();
const confirmMock = vi.fn(async () => true);

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
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

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

const sampleDrone = {
  id: 'drone-1',
  player_id: 'p1',
  team_id: null,
  drone_type: 'scout',
  name: 'Scout Alpha',
  level: 2,
  health: 80,
  max_health: 100,
  attack_power: 10,
  defense_power: 5,
  speed: 1.5,
  status: 'idle',
  sector_id: null,
  deployed_at: null as string | null,
  last_action: null,
  kills: 0,
  damage_dealt: 0,
  damage_taken: 0,
  battles_fought: 0,
  abilities: null as string | null,
  created_at: '2026-01-01T00:00:00Z',
  destroyed_at: null as string | null,
};

const sampleStats = {
  total_drones: 1,
  active_drones: 1,
  destroyed_drones: 0,
  deployed_drones: 0,
  in_combat_drones: 0,
  drones_by_type: { scout: 1 },
  average_level: 2,
  total_kills: 0,
  total_battles: 0,
};

function mockHappyGets(drones: typeof sampleDrone[] = [sampleDrone]) {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/statistics')) {
      return { data: sampleStats };
    }
    if (url.includes('/admin/drones/') && !url.match(/\/admin\/drones\/[^/]+$/)) {
      return { data: drones };
    }
    return { data: { recent_combats: [] } };
  });
}

async function renderLoaded() {
  mockHappyGets();
  render(<DroneOperationsTab />);
  await waitFor(() => {
    expect(screen.getByText('Scout Alpha')).toBeTruthy();
  });
}

/**
 * LEG-3742 Soft-ORDER — DroneOperationsTab load/act/mutate TypeError/network + HTTP honesty densify.
 */
describe('DroneOperationsTab typeErrorHonesty densify (LEG-3742)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  describe('droneLoadError (initial load)', () => {
    it('collapses axios Network Error without leaking raw transport text', async () => {
      vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));
      render(<DroneOperationsTab />);
      await waitFor(() => {
        expect(document.body.textContent).toMatch(/Failed to load drone operations data/i);
      });
      assertNoTransportLeak(document.body.textContent ?? '');
    });

    it('collapses TypeError Failed to fetch without leaking transport text', async () => {
      vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
      render(<DroneOperationsTab />);
      await waitFor(() => {
        expect(document.body.textContent).toMatch(/Failed to load drone operations data/i);
      });
      assertNoTransportLeak(document.body.textContent ?? '');
    });

    it('surfaces 401 as PLAYERS_VIEW access-denied copy', async () => {
      vi.mocked(api.get).mockRejectedValue(axiosError(401));
      render(<DroneOperationsTab />);
      await waitFor(() => {
        expect(document.body.textContent).toMatch(/PLAYERS_VIEW/i);
      });
      assertNoTransportLeak(document.body.textContent ?? '');
    });

    it('surfaces 403 as PLAYERS_VIEW access-denied copy', async () => {
      vi.mocked(api.get).mockRejectedValue(axiosError(403));
      render(<DroneOperationsTab />);
      await waitFor(() => {
        expect(document.body.textContent).toMatch(/PLAYERS_VIEW/i);
      });
      assertNoTransportLeak(document.body.textContent ?? '');
    });

    it('surfaces 429 as admin rate-limit copy on load', async () => {
      vi.mocked(api.get).mockRejectedValue(axiosError(429));
      render(<DroneOperationsTab />);
      await waitFor(() => {
        expect(document.body.textContent).toMatch(/rate limit/i);
      });
      assertNoTransportLeak(document.body.textContent ?? '');
    });
  });

  describe('droneActError (force-recall / restore)', () => {
    it('force-recall collapses Network Error to honest fallback in toast', async () => {
      mockHappyGets([{ ...sampleDrone, status: 'deployed', deployed_at: '2026-01-01T12:00:00Z' }]);
      vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));
      render(<DroneOperationsTab />);
      await waitFor(() => expect(screen.getByText('Scout Alpha')).toBeTruthy());
      fireEvent.click(screen.getByRole('button', { name: 'Force Recall' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      const msg = String(toastError.mock.calls[0][0]);
      expect(msg).toMatch(/Failed to force-recall drone/i);
      assertNoTransportLeak(msg);
    });

    it('force-recall surfaces 401 as COMBAT_INTERVENE copy', async () => {
      mockHappyGets([{ ...sampleDrone, status: 'deployed', deployed_at: '2026-01-01T12:00:00Z' }]);
      vi.mocked(api.post).mockRejectedValue(axiosError(401));
      render(<DroneOperationsTab />);
      await waitFor(() => expect(screen.getByText('Scout Alpha')).toBeTruthy());
      fireEvent.click(screen.getByRole('button', { name: 'Force Recall' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      const msg = String(toastError.mock.calls[0][0]);
      expect(msg).toMatch(/COMBAT_INTERVENE/i);
      assertNoTransportLeak(msg);
    });

    it('force-recall surfaces server detail on non-scope HTTP errors', async () => {
      mockHappyGets([{ ...sampleDrone, status: 'deployed', deployed_at: '2026-01-01T12:00:00Z' }]);
      vi.mocked(api.post).mockRejectedValue(
        axiosError(500, 'Combat override denied for this drone'),
      );
      render(<DroneOperationsTab />);
      await waitFor(() => expect(screen.getByText('Scout Alpha')).toBeTruthy());
      fireEvent.click(screen.getByRole('button', { name: 'Force Recall' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      expect(String(toastError.mock.calls[0][0])).toMatch(/Combat override denied for this drone/i);
    });

    it('restore surfaces 429 as admin rate-limit copy', async () => {
      mockHappyGets([
        {
          ...sampleDrone,
          id: 'drone-2',
          name: 'Wreck',
          status: 'destroyed',
          destroyed_at: '2026-01-02T00:00:00Z',
        },
      ]);
      vi.mocked(api.post).mockRejectedValue(axiosError(429));
      render(<DroneOperationsTab />);
      await waitFor(() => expect(screen.getByText('Wreck')).toBeTruthy());
      fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      expect(String(toastError.mock.calls[0][0])).toMatch(/rate limit/i);
    });
  });

  describe('droneMutateError (PATCH / DELETE)', () => {
    beforeEach(() => {
      vi.mocked(api.patch).mockResolvedValue({ data: { message: 'ok', drone_id: 'drone-1' } });
      vi.mocked(api.delete).mockResolvedValue({ data: { message: 'ok' } });
    });

    it('PATCH collapses TypeError Failed to fetch to honest fallback in toast', async () => {
      await renderLoaded();
      vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      const msg = String(toastError.mock.calls[0][0]);
      expect(msg).toMatch(/Failed to update drone/i);
      assertNoTransportLeak(msg);
    });

    it('PATCH surfaces 403 with SHIPS_MANAGE scope hint when no server detail', async () => {
      await renderLoaded();
      vi.mocked(api.patch).mockRejectedValue(axiosError(403));
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      const msg = String(toastError.mock.calls[0][0]);
      expect(msg).toMatch(/SHIPS_MANAGE/i);
      assertNoTransportLeak(msg);
    });

    it('PATCH surfaces server detail on 403 when provided', async () => {
      await renderLoaded();
      vi.mocked(api.patch).mockRejectedValue(
        axiosError(403, 'Drone edit forbidden for destroyed units'),
      );
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      expect(String(toastError.mock.calls[0][0])).toMatch(
        /Drone edit forbidden for destroyed units/i,
      );
    });

    it('DELETE surfaces 429 as admin rate-limit copy', async () => {
      await renderLoaded();
      vi.mocked(api.delete).mockRejectedValue(axiosError(429));
      fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      expect(String(toastError.mock.calls[0][0])).toMatch(/rate limit/i);
    });

    it('DELETE surfaces server detail via HTTP status when no scope detail', async () => {
      await renderLoaded();
      vi.mocked(api.delete).mockRejectedValue(axiosError(500, 'Drone delete conflict'));
      fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
      await waitFor(() => expect(toastError).toHaveBeenCalled());
      const msg = String(toastError.mock.calls[0][0]);
      expect(msg).toMatch(/Drone delete conflict/i);
      assertNoTransportLeak(msg);
    });
  });
});
