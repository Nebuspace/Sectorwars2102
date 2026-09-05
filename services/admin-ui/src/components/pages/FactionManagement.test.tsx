import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FactionManagement from './FactionManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const toastError = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: toastError,
    info: vi.fn(),
    warning: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => true),
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

const sampleFaction = {
  id: 'faction-1',
  name: 'Test Faction',
  faction_type: 'Federation',
  description: 'Test',
  territory_sectors: ['sector-1'],
  home_sector_id: null,
  base_pricing_modifier: 1.0,
  trade_specialties: ['ore'],
  aggression_level: 5,
  diplomacy_stance: 'neutral',
  color_primary: '#3b82f6',
  color_secondary: '#1e3a8a',
  logo_url: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function mockSuccessfulLoad() {
  vi.mocked(api.get).mockResolvedValue({ data: [sampleFaction] });
}

function createModalRoot() {
  const heading = screen.getByRole('heading', { name: 'Create Faction' });
  const modal = heading.closest('.modal');
  if (!modal) {
    throw new Error('Create faction modal not found');
  }
  return modal as HTMLElement;
}

describe('FactionManagement scope errors (LEG-968)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.factions.manage'),
    );

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.factions\.manage/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('collapses axios-shaped Network Error to load fallback (LEG-3379)', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load factions/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load factions/i).textContent ?? '';
    expect(text).toMatch(/Failed to load factions/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError on load to honest fallback (LEG-3379)', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load factions/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load factions/i).textContent ?? '';
    expect(text).toMatch(/Failed to load factions/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('FactionManagement mutation errors (LEG-2610)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    toastError.mockReset();
    mockSuccessfulLoad();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('surfaces formatAdminApiError on create POST 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.factions.manage'),
    );

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /\+ Create Faction/i }));
    const modal = createModalRoot();
    await user.type(within(modal).getAllByRole('textbox')[0], 'New Faction');
    await user.click(within(modal).getByRole('button', { name: /^Create Faction$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/factions/',
        expect.objectContaining({ name: 'New Faction' }),
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.factions.manage');
    expect(toastError).not.toHaveBeenCalledWith('Failed to create faction.');
  });

  it('surfaces rate-limit copy on create POST 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /\+ Create Faction/i }));
    const modal = createModalRoot();
    await user.type(within(modal).getAllByRole('textbox')[0], 'New Faction');
    await user.click(within(modal).getByRole('button', { name: /^Create Faction$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Failed to create faction.');
  });

  it('surfaces formatAdminApiError on edit PUT 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(
      axiosError(403, 'Missing scope admin.factions.manage'),
    );

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/factions/${sampleFaction.id}`,
        expect.objectContaining({ name: sampleFaction.name }),
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.factions.manage');
    expect(toastError).not.toHaveBeenCalledWith('Failed to update faction.');
  });

  it('surfaces rate-limit copy on edit PUT 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(axiosError(429));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith('Failed to update faction.');
  });

  it('collapses axios-shaped Network Error on create POST to honest fallback (LEG-3379)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /\+ Create Faction/i }));
    const modal = createModalRoot();
    await user.type(within(modal).getAllByRole('textbox')[0], 'New Faction');
    await user.click(within(modal).getByRole('button', { name: /^Create Faction$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to create faction/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError on create POST to honest fallback (LEG-3379)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /\+ Create Faction/i }));
    const modal = createModalRoot();
    await user.type(within(modal).getAllByRole('textbox')[0], 'New Faction');
    await user.click(within(modal).getByRole('button', { name: /^Create Faction$/i }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to create faction/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error on edit PUT to honest fallback (LEG-3379)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(new Error('Network Error'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to update faction/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('surfaces honest fallback on edit PUT TypeError/network collapse (LEG-2971)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Edit$/i }));
    await user.click(screen.getByRole('button', { name: /Save Changes/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/factions/${sampleFaction.id}`,
        expect.objectContaining({ name: sampleFaction.name }),
      );
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to update faction/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses axios-shaped Network Error on territory PUT to honest fallback (LEG-3379)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(new Error('Network Error'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Territory$/i }));
    await user.click(screen.getByRole('button', { name: /Save Territory/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to update territory/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError on territory PUT to honest fallback (LEG-3379)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Territory$/i }));
    await user.click(screen.getByRole('button', { name: /Save Territory/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to update territory/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('surfaces formatAdminApiError on territory PUT 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(
      axiosError(403, 'Missing scope admin.factions.territory'),
    );

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Territory$/i }));
    await user.click(screen.getByRole('button', { name: /Save Territory/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/factions/${sampleFaction.id}/territory`,
        expect.objectContaining({ sector_ids: sampleFaction.territory_sectors }),
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.factions.territory');
    expect(toastError).not.toHaveBeenCalledWith(
      'Failed to update territory. Check that sector IDs are valid.',
    );
  });

  it('surfaces rate-limit copy on territory PUT 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(axiosError(429));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Territory$/i }));
    await user.click(screen.getByRole('button', { name: /Save Territory/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/factions/${sampleFaction.id}/territory`,
        expect.objectContaining({ sector_ids: sampleFaction.territory_sectors }),
      );
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith(
      'Failed to update territory. Check that sector IDs are valid.',
    );
  });

  it('collapses axios-shaped Network Error on reputation PUT to honest fallback (LEG-3379)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(new Error('Network Error'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Reputation$/i }));
    await user.type(screen.getByPlaceholderText('Player UUID'), 'player-1');
    await user.click(screen.getByRole('button', { name: /Apply Change/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to adjust reputation/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError on reputation PUT to honest fallback (LEG-3379)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Reputation$/i }));
    await user.type(screen.getByPlaceholderText('Player UUID'), 'player-1');
    await user.click(screen.getByRole('button', { name: /Apply Change/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls.map((call) => call[0]).join('\n'));
    expect(msg).toMatch(/Failed to adjust reputation/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('surfaces formatAdminApiError on reputation PUT 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(
      axiosError(403, 'Missing scope admin.factions.reputation'),
    );

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Reputation$/i }));
    await user.type(screen.getByPlaceholderText('Player UUID'), 'player-1');
    await user.click(screen.getByRole('button', { name: /Apply Change/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalledWith(
        `/api/v1/admin/factions/${sampleFaction.id}/reputation`,
        expect.objectContaining({
          player_id: 'player-1',
          change: 10,
        }),
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.factions.reputation');
    expect(toastError).not.toHaveBeenCalledWith(
      'Failed to adjust reputation. Check the player ID.',
    );
  });

  it('surfaces rate-limit copy on reputation PUT 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.put).mockRejectedValue(axiosError(429));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /^Reputation$/i }));
    await user.type(screen.getByPlaceholderText('Player UUID'), 'player-1');
    await user.click(screen.getByRole('button', { name: /Apply Change/i }));

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    expect(toastError).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    expect(toastError).not.toHaveBeenCalledWith(
      'Failed to adjust reputation. Check the player ID.',
    );
  });
});
