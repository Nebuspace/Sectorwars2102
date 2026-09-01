import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
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

/**
 * LEG-3486 Soft-ORDER — PlanetsManager TypeError/Network Error honesty densify.
 */
describe('PlanetsManager typeErrorHonesty densify (LEG-3486)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
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
});
