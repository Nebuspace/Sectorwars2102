import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlanetDetailModal from './PlanetDetailModal';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
  },
}));

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: {} },
  });

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
  expect(text).not.toMatch(/^HTTP \d+$/);
  expect(text).not.toContain('Request failed with status code');
}

const planet = {
  id: 'p1',
  name: 'Terra',
  sector_id: 's1',
  planet_type: 'TERRAN',
  population: 100,
  max_population: 1000,
  defense_level: 1,
  habitability_score: 50,
  resource_richness: 1,
  gravity: 1,
  created_at: '2026-01-01T00:00:00Z',
};

/**
 * LEG-3701 Soft-ORDER — PlanetDetailModal TypeError/Network Error honesty densify.
 * LEG-3942 Soft-ORDER — HTTP 403/429 densify via formatUniverseAdminError.
 */
describe('PlanetDetailModal typeErrorHonesty densify (LEG-3701)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { holdings: [] } });
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on planet save without leaking raw transport text', async () => {
    vi.mocked(api.patch).mockRejectedValue(new Error('Network Error'));

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to save planet changes/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to save planet changes/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on planet save without leaking transport text', async () => {
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to save planet changes/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to save planet changes/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('surfaces admin.universe.manage on planet save 403 without transport leak', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(403));

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/Access denied/i)).toBeTruthy();
    });
    const text = screen.getByText(/Access denied/i).textContent ?? '';
    expect(text).toMatch(/admin\.universe\.manage/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces rate-limit on planet save 429 without transport leak', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(429));

    render(
      <PlanetDetailModal
        isOpen
        planet={planet as any}
        onClose={() => {}}
        mode="edit"
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });
});
