import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import BangGalaxyPage from './BangGalaxyPage';

const wipeGalaxy = vi.fn();
const loadGalaxyInfo = vi.fn();
const loadBangHistory = vi.fn();
const addPlayerOwnedRegion = vi.fn();

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { error?: string }) =>
      opts?.error ? `${key}:${opts.error}` : key,
  }),
}));

vi.mock('../../contexts/AdminContext', () => ({
  useAdmin: () => ({
    galaxyState: { id: 'g1', name: 'TestGalaxy' },
    loadGalaxyInfo,
    wipeGalaxy,
    bangHistory: [],
    bangHistoryTotal: 0,
    loadBangHistory,
    isLoading: false,
  }),
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ token: 'tok' }),
}));

vi.mock('../../services/bangGalaxyApi', () => ({
  addPlayerOwnedRegion: (...args: unknown[]) => addPlayerOwnedRegion(...args),
}));

vi.mock('../universe/bang/GalaxyGenerationForm', () => ({
  default: () => <div data-testid="form-stub" />,
}));

vi.mock('../universe/bang/GenerationLogPanel', () => ({
  default: () => null,
}));

vi.mock('../universe/bang/AddRegionDialog', () => ({
  default: ({
    onConfirm,
    error,
  }: {
    onConfirm: (seed: number, sectors: number) => void;
    error: string | null;
  }) => (
    <div>
      <button type="button" onClick={() => onConfirm(42, 100)}>
        Confirm add region
      </button>
      {error ? <div role="alert">{error}</div> : null}
    </div>
  ),
}));

vi.mock('../universe/bang/GalaxyOverviewHeader', () => ({
  default: ({
    onWipe,
    onAddRegion,
  }: {
    onWipe?: () => void;
    onAddRegion?: () => void;
  }) => (
    <div>
      <button type="button" onClick={onWipe}>
        Open wipe
      </button>
      <button type="button" onClick={onAddRegion}>
        Open add region
      </button>
    </div>
  ),
}));

vi.mock('../universe/bang/WipeGalaxyConfirmDialog', () => ({
  default: ({
    onConfirm,
    error,
  }: {
    onConfirm: (name: string) => void;
    error: string | null;
  }) => (
    <div>
      <button type="button" onClick={() => onConfirm('TestGalaxy')}>
        Confirm wipe
      </button>
      {error ? <div role="alert">{error}</div> : null}
    </div>
  ),
}));

/**
 * LEG-3664 Soft-ORDER — BangGalaxyPage TypeError/Network Error densify.
 */
describe('BangGalaxyPage typeErrorHonesty densify (LEG-3664)', () => {
  beforeEach(() => {
    wipeGalaxy.mockReset();
    loadGalaxyInfo.mockReset();
    loadBangHistory.mockReset();
    addPlayerOwnedRegion.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('collapses axios Network Error on wipe without leaking raw transport text', async () => {
    wipeGalaxy.mockRejectedValue(new Error('Network Error'));

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Open wipe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm wipe' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text.length).toBeGreaterThan(0);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on wipe without leaking transport text', async () => {
    wipeGalaxy.mockRejectedValue(new TypeError('Failed to fetch'));

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Open wipe' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm wipe' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on add-region without leaking raw transport text', async () => {
    addPlayerOwnedRegion.mockRejectedValue(new Error('Network Error'));

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Open add region' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm add region' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to add player-owned region/i);
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on add-region without leaking transport text', async () => {
    addPlayerOwnedRegion.mockRejectedValue(new TypeError('Failed to fetch'));

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Open add region' }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm add region' }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const text = screen.getByRole('alert').textContent ?? '';
    expect(text).toMatch(/Failed to add player-owned region/i);
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on history load without leaking raw transport text', async () => {
    loadBangHistory.mockRejectedValue(new Error('Network Error'));

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('tab', { name: 'bang.page.tabHistory' }));

    await waitFor(() => {
      expect(loadBangHistory).toHaveBeenCalledWith(0, 20);
    });
    await waitFor(() => {
      expect(screen.getByText(/Failed to load history/i)).toBeTruthy();
    });
    const text = screen.getByText(/bang\.history\.loadFailed/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on history load without leaking transport text', async () => {
    loadBangHistory.mockRejectedValue(new TypeError('Failed to fetch'));

    render(<BangGalaxyPage />);
    fireEvent.click(screen.getByRole('tab', { name: 'bang.page.tabHistory' }));

    await waitFor(() => {
      expect(loadBangHistory).toHaveBeenCalledWith(0, 20);
    });
    await waitFor(() => {
      expect(screen.getByText(/Failed to load history/i)).toBeTruthy();
    });
    const text = screen.getByText(/bang\.history\.loadFailed/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
