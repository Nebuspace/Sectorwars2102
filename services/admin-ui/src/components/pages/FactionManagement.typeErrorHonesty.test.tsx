import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FactionManagement from './FactionManagement';
import { api } from '../../utils/auth';

const toastError = vi.fn();

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

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

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

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

/**
 * LEG-3427 Soft-ORDER — FactionManagement TypeError/Network Error honesty densify.
 * LEG-3839 Soft-ORDER — 403/429 HTTP honesty densify (load + mutation).
 */
describe('FactionManagement typeErrorHonesty densify (LEG-3427)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error to load fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load factions/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load factions/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch to load fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load factions/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load factions/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});

describe('FactionManagement typeErrorHonesty densify (LEG-3839)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces 403 on factions list load with scope-aware copy when no server detail', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/admin faction management scopes required/i)).toBeTruthy();
    });

    const text = screen.getByText(/admin faction management scopes required/i).textContent ?? '';
    expect(text).toMatch(/Access denied/i);
    expect(text).not.toMatch(/\b403\b/);
    assertNoTransportLeak(text);
  });

  it('surfaces server detail on 403 list load when provided', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.factions.manage'),
    );

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.factions\.manage/i)).toBeTruthy();
    });

    const text = screen.getByText(/Missing scope admin\.factions\.manage/i).textContent ?? '';
    expect(text).not.toMatch(/\b403\b/);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 on factions list load as admin rate-limit copy', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<FactionManagement />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });

    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).not.toMatch(/\b429\b/);
    assertNoTransportLeak(text);
  });

  it('surfaces create POST 403 with scope-aware friendly copy via formatAdminApiError', async () => {
    const user = userEvent.setup();
    mockSuccessfulLoad();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    render(<FactionManagement />);
    await waitFor(() => expect(screen.getByText('Test Faction')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: /\+ Create Faction/i }));
    const modal = createModalRoot();
    await user.type(within(modal).getAllByRole('textbox')[0], 'New Faction');
    await user.click(within(modal).getByRole('button', { name: /^Create Faction$/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    const msg = String(toastError.mock.calls[0][0]);
    expect(msg).toMatch(/admin faction management scopes required/i);
    expect(msg).toMatch(/Access denied/i);
    expect(msg).not.toMatch(/\b403\b/);
    assertNoTransportLeak(msg);
  });
});
