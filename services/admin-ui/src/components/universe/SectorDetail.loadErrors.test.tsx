import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import SectorDetail from './SectorDetail';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    error: vi.fn(),
    success: vi.fn(),
    info: vi.fn(),
  }),
}));

const sector = {
  id: 'sec-uuid-1',
  sector_id: 42,
  name: 'Alpha',
  type: 'STANDARD',
  x_coord: 0,
  y_coord: 0,
  z_coord: 0,
  hazard_level: 1,
  is_discovered: true,
  has_port: false,
  has_planet: false,
  controlling_faction: null,
};

const axiosError = (status: number) =>
  Object.assign(new Error(`HTTP ${status}`), { response: { status } });

describe('SectorDetail load scope errors (LEG-2820)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
  });

  it('surfaces admin.universe.manage on port load 403', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/admin\.universe\.manage|Access denied/i);
  });

  it('surfaces rate-limit on ships load 429', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/ships')) {
        throw axiosError(429);
      }
      throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
    });

    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/rate limit/i);
  });

  it('surfaces honest fallback on port load TypeError/network collapse (LEG-3065)', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/port')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.endsWith('/ships')) {
        return { data: { ships: [] } };
      }
      throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
    });

    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeTruthy();
    });
    const alert = screen.getByRole('alert').textContent ?? '';
    expect(alert).toMatch(/Failed to load port data/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
    expect(alert).not.toMatch(/TypeError/i);
  });
});
