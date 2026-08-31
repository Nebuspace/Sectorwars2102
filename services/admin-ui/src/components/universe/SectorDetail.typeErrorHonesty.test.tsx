import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import SectorDetail from './SectorDetail';
import { api } from '../../utils/auth';

const toastError = vi.hoisted(() => vi.fn());

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    error: toastError,
    success: vi.fn(),
    info: vi.fn(),
    warning: vi.fn(),
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

function mockSuccessfulLoads() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/port') || url.endsWith('/planet')) {
      throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
    }
    if (url.endsWith('/ships')) {
      return { data: { ships: [] } };
    }
    throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
  });
}

async function saveName(value: string) {
  const nameLabel = screen.getByText('Name:');
  const row = nameLabel.closest('div');
  const clickable = row?.querySelector('.editable-field.clickable') as HTMLElement | null;
  expect(clickable).toBeTruthy();
  fireEvent.click(clickable!);
  const input = await screen.findByDisplayValue('Alpha');
  fireEvent.change(input, { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: '✓' }));
}

/**
 * LEG-3482 Soft-ORDER — SectorDetail TypeError/Network Error honesty densify.
 * Covers loadError alert + mutation toast paths via formatUniverseAdminError.
 */
describe('SectorDetail typeErrorHonesty densify (LEG-3482)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.put).mockReset();
    toastError.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on port load to honest loadError fallback', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/port')) {
        throw new Error('Network Error');
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
    expect(alert).not.toBe('Network Error');
    expect(alert).not.toContain('Network Error');
    expect(alert).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on ships load to honest loadError fallback', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/ships')) {
        throw new TypeError('Failed to fetch');
      }
      if (url.endsWith('/port') || url.endsWith('/planet')) {
        throw Object.assign(new Error('HTTP 404'), { response: { status: 404 } });
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
    expect(alert).toMatch(/Failed to load ships data/i);
    expect(alert).not.toMatch(/TypeError/i);
    expect(alert).not.toMatch(/Failed to fetch/i);
  });

  it('collapses axios Network Error on name PUT to update-name toast fallback', async () => {
    mockSuccessfulLoads();
    vi.mocked(api.put).mockRejectedValue(new Error('Network Error'));

    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Name:')).toBeTruthy();
    });
    await saveName('Renamed Network');

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0]?.[0] ?? '');
    expect(msg).toMatch(/Failed to update name/i);
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on name PUT to update-name toast fallback', async () => {
    mockSuccessfulLoads();
    vi.mocked(api.put).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <SectorDetail
        sector={sector}
        onBack={() => undefined}
        onPortClick={() => undefined}
        onPlanetClick={() => undefined}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Name:')).toBeTruthy();
    });
    await saveName('Renamed TypeError');

    await waitFor(() => {
      expect(api.put).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(toastError).toHaveBeenCalled();
    });
    const msg = String(toastError.mock.calls[0]?.[0] ?? '');
    expect(msg).toMatch(/Failed to update name/i);
    expect(msg).not.toMatch(/TypeError/i);
    expect(msg).not.toMatch(/Failed to fetch/i);
  });
});
