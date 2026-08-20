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

describe('CombatOverview restore_shields honesty (LEG-482)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockLoad();
  });

  it('does not claim ships changed when restore_shields note says they were not', async () => {
    vi.mocked(api.post).mockResolvedValue({
      data: {
        result: {
          action: 'shields_restored',
          note: 'Shield restoration would be applied to ship models',
        },
      },
    });
    const user = userEvent.setup();
    render(<CombatOverview />);

    await waitFor(() => {
      expect(screen.getByText('Open intervention')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Open intervention'));
    expect(screen.getByText('Log shield restore (audit only)')).toBeInTheDocument();
    expect(screen.queryByText('Restore Ships')).not.toBeInTheDocument();

    await user.click(screen.getByText('Log shield restore (audit only)'));

    await waitFor(() => {
      expect(
        screen.getByText('Shield restoration would be applied to ship models')
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/hull/i)).not.toBeInTheDocument();
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
