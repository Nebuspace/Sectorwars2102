import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlanetsManager from './PlanetsManager';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../universe/PlanetDetailModal', () => ({
  default: () => null,
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

const samplePlanet = {
  id: 'pl-1',
  name: 'Terra',
  sector_id: '1',
  sector_name: 'Alpha',
  planet_type: 'Terran',
  population: 1000,
  max_population: 10000,
  defense_level: 1,
  created_at: '2026-01-01T00:00:00Z',
  is_habitable: true,
};

/**
 * LEG-3486 Soft-ORDER — PlanetsManager TypeError/Network Error honesty densify.
 * LEG-3869 Soft-ORDER — 403/429 HTTP honesty densify.
 */
describe('PlanetsManager typeErrorHonesty densify (LEG-3486)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.delete).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on load to planets fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<PlanetsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch planets/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to fetch planets/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on load to planets fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PlanetsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to fetch planets/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to fetch planets/i).textContent ?? '';
    expect(text).not.toMatch(/TypeError/i);
    expect(text).not.toBe('Failed to fetch');
  });

  it('surfaces 403 with friendly scope copy when planets list GET is denied', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<PlanetsManager />);

    await waitFor(() => {
      expect(screen.getByText(/Access denied|planet management/i)).toBeTruthy();
    });
    const text = screen.getByText(/Access denied|planet management/i).textContent ?? '';
    expect(text).toMatch(/Access denied/i);
    expect(text).not.toMatch(/\b403\b/);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on planets list GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<PlanetsManager />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    const text = screen.getByText(/rate limit/i).textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/\b429\b/);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

  it('surfaces planet delete mutation 403 with formatAdminApiError-friendly copy', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    vi.mocked(api.get).mockResolvedValue({
      data: { planets: [samplePlanet], total_count: 1 },
    });
    vi.mocked(api.delete).mockRejectedValue(axiosError(403));

    render(<PlanetsManager />);

    await waitFor(() => {
      expect(screen.getByText('Terra')).toBeTruthy();
    });

    const deleteBtn =
      screen.queryByRole('button', { name: /Delete/i }) ??
      screen.queryByTitle(/Delete/i) ??
      screen.getByText(/Delete/i);
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(api.delete).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByText(/Access denied|planet management/i)).toBeTruthy();
    });
    const text = screen.getByText(/Access denied|planet management/i).textContent ?? '';
    expect(text).toMatch(/Access denied/i);
    expect(text).not.toMatch(/\b403\b/);
    assertNoTransportLeak(text);
    vi.unstubAllGlobals();
  });
});
