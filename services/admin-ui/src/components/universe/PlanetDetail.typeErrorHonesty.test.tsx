import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PlanetDetail from './PlanetDetail';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    patch: vi.fn(),
  },
}));

vi.mock('../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    getIcon: (k: string) => k,
    getLabel: (k: string) => k,
  }),
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

const basePlanet = {
  id: 'planet-1',
  name: 'Terra Nova',
  planet_type: 'TERRAN',
  owner_id: '11111111-1111-1111-1111-111111111111',
  owner_name: 'Ada Colony',
  citadel_level: 1,
  shield_level: 1,
  drones: 10,
  breeding_rate: 5,
  defense_level: 2,
  colonists: { fuel: 100, organics: 50, equipment: 25 },
  production: { ore: 3, organics: 2, equipment: 1 },
};

async function saveName(value: string) {
  const user = userEvent.setup();
  const nameLabel = screen.getByText('Name:');
  const row = nameLabel.closest('.info-item');
  await user.click(row!.querySelector('.editable-field.clickable') as HTMLElement);
  const input = screen.getByRole('textbox');
  await user.clear(input);
  await user.type(input, value);
  await user.click(screen.getByRole('button', { name: '✓' }));
}

/**
 * LEG-3456 Soft-ORDER — PlanetDetail TypeError/Network Error honesty densify.
 * LEG-3939 Soft-ORDER — HTTP 403/429 densify via formatAdminApiError.
 */
describe('PlanetDetail typeErrorHonesty densify (LEG-3456)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on name PATCH to update-name fallback', async () => {
    vi.mocked(api.patch).mockRejectedValue(new Error('Network Error'));

    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);
    await saveName('Network Collapse');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).toMatch(/Failed to update name/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on name PATCH to update-name fallback', async () => {
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);
    await saveName('TypeError Collapse');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).toMatch(/Failed to update name/i);
    expect(msg).not.toMatch(/TypeError/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
  });

  it('surfaces 403 with admin.universe.manage scope copy on name PATCH', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(403));

    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);
    await saveName('Forbidden Rename');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).toMatch(/Access denied/i);
    expect(msg).toMatch(/admin\.universe\.manage/i);
    expect(msg).not.toMatch(/\b403\b/);
    expect(msg).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(msg);
  });

  it('surfaces 429 as admin rate-limit copy on name PATCH', async () => {
    vi.mocked(api.patch).mockRejectedValue(axiosError(429));

    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);
    await saveName('Rate Limited');

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).toMatch(/rate limit/i);
    expect(msg).not.toMatch(/\b429\b/);
    expect(msg).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(msg);
  });
});
