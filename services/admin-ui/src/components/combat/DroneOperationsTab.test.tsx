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

describe('DroneOperationsTab scope errors', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
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

describe('DroneOperationsTab PATCH + DELETE (LEG-1683)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.mocked(api.patch).mockResolvedValue({ data: { message: 'ok', drone_id: 'drone-1' } });
    vi.mocked(api.delete).mockResolvedValue({ data: { message: 'ok' } });
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('Edit save PATCHes /api/v1/admin/drones/{id}', async () => {
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/api/v1/admin/drones/drone-1',
        expect.objectContaining({
          name: 'Scout Alpha',
          level: 2,
          health: 80,
          max_health: 100,
          attack_power: 10,
          defense_power: 5,
          speed: 1.5,
          status: 'idle',
        })
      );
    });
  });

  it('Delete (confirm true) DELETEs /api/v1/admin/drones/{id}', async () => {
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalledWith(
        expect.objectContaining({ danger: true, confirmLabel: 'Delete' })
      );
      expect(api.delete).toHaveBeenCalledWith('/api/v1/admin/drones/drone-1');
    });
  });

  it('Delete does not call api.delete when confirm is false', async () => {
    confirmMock.mockResolvedValue(false);
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => {
      expect(confirmMock).toHaveBeenCalled();
    });
    expect(api.delete).not.toHaveBeenCalled();
  });

  it('PATCH 403 toasts SHIPS_MANAGE, not generic Failed', async () => {
    vi.mocked(api.patch).mockRejectedValue({ response: { status: 403 } });
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/SHIPS_MANAGE/);
    expect(msg).not.toMatch(/Failed to update drone\.$/);
  });

  it('PATCH 429 toasts admin rate-limit', async () => {
    vi.mocked(api.patch).mockRejectedValue({ response: { status: 429 } });
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/rate limit/i);
  });

  it('DELETE 403 toasts SHIPS_MANAGE', async () => {
    vi.mocked(api.delete).mockRejectedValue({ response: { status: 403 } });
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/SHIPS_MANAGE/);
  });

  it('DELETE 429 toasts admin rate-limit', async () => {
    vi.mocked(api.delete).mockRejectedValue({ response: { status: 429 } });
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/rate limit/i);
  });

  it('Edit save from Destroyed table PATCHes /api/v1/admin/drones/{id}', async () => {
    mockHappyGets([
      {
        ...sampleDrone,
        id: 'drone-2',
        name: 'Wreck',
        status: 'destroyed',
        destroyed_at: '2026-01-02T00:00:00Z',
      },
    ]);
    render(<DroneOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText('Wreck')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith(
        '/api/v1/admin/drones/drone-2',
        expect.objectContaining({ name: 'Wreck', status: 'destroyed' })
      );
    });
  });
});

describe('DroneOperationsTab POST mutations (LEG-2763)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.delete).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    confirmMock.mockReset();
    confirmMock.mockResolvedValue(true);
  });

  it('force-recall POST 403 toasts COMBAT_INTERVENE, not generic Failed', async () => {
    mockHappyGets([
      {
        ...sampleDrone,
        status: 'deployed',
        deployed_at: '2026-01-01T12:00:00Z',
      },
    ]);
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403 } });

    render(<DroneOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText('Scout Alpha')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Force Recall' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/drones/drone-1/force-recall');
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/COMBAT_INTERVENE/);
    expect(msg).not.toMatch(/Failed to force-recall drone\.$/);
  });

  it('force-recall POST 429 toasts admin rate-limit', async () => {
    mockHappyGets([
      {
        ...sampleDrone,
        status: 'deployed',
        deployed_at: '2026-01-01T12:00:00Z',
      },
    ]);
    vi.mocked(api.post).mockRejectedValue({ response: { status: 429 } });

    render(<DroneOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText('Scout Alpha')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Force Recall' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/rate limit/i);
  });

  it('restore POST 403 toasts COMBAT_INTERVENE, not generic Failed', async () => {
    mockHappyGets([
      {
        ...sampleDrone,
        id: 'drone-2',
        name: 'Wreck',
        status: 'destroyed',
        destroyed_at: '2026-01-02T00:00:00Z',
      },
    ]);
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403 } });

    render(<DroneOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText('Wreck')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith('/api/v1/admin/drones/drone-2/restore');
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/COMBAT_INTERVENE/);
    expect(msg).not.toMatch(/Failed to restore drone\.$/);
  });

  it('restore POST 429 toasts admin rate-limit', async () => {
    mockHappyGets([
      {
        ...sampleDrone,
        id: 'drone-2',
        name: 'Wreck',
        status: 'destroyed',
        destroyed_at: '2026-01-02T00:00:00Z',
      },
    ]);
    vi.mocked(api.post).mockRejectedValue({ response: { status: 429 } });

    render(<DroneOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText('Wreck')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    expect(String(toastError.mock.calls[0][0])).toMatch(/rate limit/i);
  });
});
