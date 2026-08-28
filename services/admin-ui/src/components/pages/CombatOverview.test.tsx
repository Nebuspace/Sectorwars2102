import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CombatOverview } from './CombatOverview';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/WebSocketContext', () => ({
  useCombatUpdates: () => undefined,
}));

vi.mock('../charts/CombatActivityChart', () => ({
  CombatActivityChart: () => null,
}));

vi.mock('../combat/CombatFeed', () => ({
  CombatFeed: ({ onInterventionClick }: { onInterventionClick: (id: string) => void }) => (
    <button type="button" onClick={() => onInterventionClick('combat-1')}>
      Open intervention
    </button>
  ),
}));

vi.mock('../combat/DisputePanel', () => ({
  DisputePanel: () => null,
}));

vi.mock('../combat/DroneOperationsTab', () => ({
  default: () => null,
}));

vi.mock('../combat/BalanceAnalytics', () => ({
  default: () => null,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const emptyStats = {
  timestamp: null,
  active_combats: { total: 1, by_type: {}, needing_intervention: 1 },
  balance_summary: {
    score: 0,
    total_combats_24h: 0,
    outliers_count: 0,
    top_recommendation: '',
  },
  dispute_summary: {
    total_disputes: 0,
    by_severity: { critical: 0, high: 0, medium: 0, low: 0 },
    critical_disputes: [],
  },
  recent_combats: [],
};

function mockLoad() {
  vi.mocked(api.get).mockResolvedValue({ data: [] });
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('dashboard-summary')) return { data: emptyStats };
    return { data: [] };
  });
}

describe('CombatOverview restore_shields honesty (LEG-482 / LEG-1348)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockLoad();
  });

  it('labels restore as live shield write and posts shield_percent + target', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        result: {
          action: 'shields_restored',
          note: 'Restored shields to 50% of max_shields on 2 ship(s)',
        },
      },
    });
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Open intervention'));
    expect(screen.getByText('Restore shields')).toBeInTheDocument();
    expect(screen.queryByText(/audit only/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Ship\.shields writes land/i)).not.toBeInTheDocument();

    await user.click(screen.getByText('Restore shields'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/combat/combat-1/intervene',
        expect.objectContaining({
          intervention_type: 'restore_shields',
          parameters: expect.objectContaining({
            target: 'both',
            shield_percent: 50,
          }),
        })
      );
    });
    await waitFor(() => {
      expect(
        screen.getByText('Restored shields to 50% of max_shields on 2 ship(s)')
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/does not change ship/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Logged only/i)).not.toBeInTheDocument();
  });

  it('posts custom restore target and shield_percent', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { result: { action: 'shields_restored', note: 'Restored shields to 75% of max_shields on 1 ship(s)' } },
    });
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.selectOptions(screen.getByLabelText('Restore shields target'), 'attacker');
    await user.clear(screen.getByLabelText('Restore shield percent'));
    await user.type(screen.getByLabelText('Restore shield percent'), '75');
    await user.click(screen.getByText('Restore shields'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/combat/combat-1/intervene',
        expect.objectContaining({
          intervention_type: 'restore_shields',
          parameters: expect.objectContaining({
            target: 'attacker',
            shield_percent: 75,
          }),
        })
      );
    });
  });

  it('posts adjust_damage with target and multiplier', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { result: { action: 'damage_adjusted', target: 'defender', multiplier: 0.5 } },
    });
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.selectOptions(screen.getByLabelText('Adjust damage target'), 'defender');
    await user.clear(screen.getByLabelText('Damage multiplier'));
    await user.type(screen.getByLabelText('Damage multiplier'), '0.5');
    await user.click(screen.getByText('Adjust damage'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/combat/combat-1/intervene',
        expect.objectContaining({
          intervention_type: 'adjust_damage',
          parameters: expect.objectContaining({
            target: 'defender',
            damage_multiplier: 0.5,
          }),
        })
      );
    });
  });

  it('posts declare_winner with winner side', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { result: { action: 'winner_declared', winner: 'defender' } },
    });
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.selectOptions(screen.getByLabelText('Declare winner side'), 'defender');
    await user.click(screen.getByRole('button', { name: 'Declare winner' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/combat/combat-1/intervene',
        expect.objectContaining({
          intervention_type: 'declare_winner',
          parameters: expect.objectContaining({
            winner: 'defender',
          }),
        })
      );
    });
  });

  it('keeps Force End path posting stop_combat', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: { result: { action: 'stopped' }, message: 'ended' },
    });
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.click(screen.getByText('Force End Combat'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/combat/combat-1/intervene',
        expect.objectContaining({ intervention_type: 'stop_combat' })
      );
    });
  });
});

describe('CombatOverview scope errors (LEG-921)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces 403 scope detail when combat endpoints deny access', async () => {
    const err403 = Object.assign(new Error('HTTP 403'), {
      response: {
        status: 403,
        data: { detail: 'Missing scope admin.combat.view' },
      },
    });
    vi.mocked(api.get).mockRejectedValue(err403);

    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText(/admin\.combat\.view|Missing scope/i)).toBeTruthy();
    });
  });
});

describe('CombatOverview intervention mutation formatAdminApiError (LEG-2599)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  async function postRestoreShields(user: ReturnType<typeof userEvent.setup>) {
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Open intervention'));
    await user.click(screen.getByText('Restore shields'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/combat/combat-1/intervene',
        expect.objectContaining({ intervention_type: 'restore_shields' })
      );
    });
  }

  it('surfaces 403 detail on restore_shields intervention post', async () => {
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.combat.intervene' },
        },
      })
    );
    const user = userEvent.setup();
    await postRestoreShields(user);

    await waitFor(() => {
      expect(screen.getByText('Missing scope admin.combat.intervene')).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to intervene in combat')).not.toBeInTheDocument();
  });

  it('surfaces scope hint on restore_shields intervention 403 without detail', async () => {
    vi.mocked(api.post).mockRejectedValue({
      response: { status: 403, data: {} },
    });
    const user = userEvent.setup();
    await postRestoreShields(user);

    await waitFor(() => {
      expect(
        screen.getByText(/admin combat intervention scope required|Access denied/i)
      ).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to intervene in combat')).not.toBeInTheDocument();
  });

  it('surfaces rate-limit copy on restore_shields intervention 429', async () => {
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), {
        response: { status: 429 },
      })
    );
    const user = userEvent.setup();
    await postRestoreShields(user);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to intervene in combat')).not.toBeInTheDocument();
  });

  it('surfaces 403 detail on adjust_damage intervention post', async () => {
    vi.mocked(api.post).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), {
        response: {
          status: 403,
          data: { detail: 'Missing scope admin.combat.intervene' },
        },
      })
    );
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.click(screen.getByText('Adjust damage'));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/combat/combat-1/intervene',
        expect.objectContaining({ intervention_type: 'adjust_damage' })
      );
    });
    await waitFor(() => {
      expect(screen.getByText('Missing scope admin.combat.intervene')).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to intervene in combat')).not.toBeInTheDocument();
  });
});

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

describe('CombatOverview intervention scope errors (LEG-2599)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('restore_shields 403 surfaces formatAdminApiError intervention scope copy', async () => {
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.combat.intervene')
    );
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.click(screen.getByText('Restore shields'));

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.combat\.intervene/i)).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to intervene in combat')).not.toBeInTheDocument();
  });

  it('restore_shields 429 surfaces admin rate-limit helper copy', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.click(screen.getByText('Restore shields'));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeInTheDocument();
    });
  });

  it('adjust_damage 403 surfaces formatAdminApiError intervention scope copy', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(403));
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Open intervention'));
    await user.click(screen.getByText('Adjust damage'));

    await waitFor(() => {
      expect(
        screen.getByText(/admin combat intervention scope required|Access denied/i)
      ).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to intervene in combat')).not.toBeInTheDocument();
  });
});
