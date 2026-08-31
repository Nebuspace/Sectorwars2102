import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import FleetOperationsTab from './FleetOperationsTab';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

/**
 * LEG-3438 Soft-ORDER — FleetOperationsTab TypeError/Network Error honesty densify.
 */
describe('FleetOperationsTab typeErrorHonesty densify (LEG-3438)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on load to fleet-ops fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load fleet operations data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load fleet operations data/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on load to fleet-ops fallback', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));
    render(<FleetOperationsTab />);
    await waitFor(() => {
      expect(screen.getByText(/Failed to load fleet operations data/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load fleet operations data/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
