import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import SectorEditModal from './SectorEditModal';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../../contexts/ToastContext', () => ({
  useConfirm: () => vi.fn(async () => true),
}));

const sector = {
  id: 'sec-1',
  sector_id: 1,
  name: 'Alpha',
  type: 'STANDARD',
  cluster_id: 'c1',
  x_coord: 0,
  y_coord: 0,
  z_coord: 0,
  hazard_level: 1,
  is_discovered: true,
  has_port: false,
  has_planet: false,
  has_warp_tunnel: false,
  player_count: 0,
  controlling_faction: null,
};

function mockSuccessfulDetailLoads() {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.endsWith('/planet')) {
      return { data: { has_planet: false, planet: null } };
    }
    if (url.endsWith('/port')) {
      return { data: { has_port: false, port: null } };
    }
    throw new Error(`unexpected GET ${url}`);
  });
}

/**
 * LEG-3702 Soft-ORDER — SectorEditModal TypeError/Network Error honesty densify.
 */
describe('SectorEditModal typeErrorHonesty densify (LEG-3702)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on sector save without leaking raw transport text', async () => {
    mockSuccessfulDetailLoads();
    vi.mocked(api.put).mockRejectedValue(new Error('Network Error'));

    render(
      <MemoryRouter>
        <SectorEditModal
          isOpen
          sector={sector as any}
          onClose={() => {}}
          onSave={() => {}}
        />
      </MemoryRouter>,
    );

    const nameInput = await screen.findByDisplayValue('Alpha');
    fireEvent.change(nameInput, { target: { value: 'Beta' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to update sector/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to update sector/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses TypeError Failed to fetch on sector save without leaking transport text', async () => {
    mockSuccessfulDetailLoads();
    vi.mocked(api.put).mockRejectedValue(new TypeError('Failed to fetch'));

    render(
      <MemoryRouter>
        <SectorEditModal
          isOpen
          sector={sector as any}
          onClose={() => {}}
          onSave={() => {}}
        />
      </MemoryRouter>,
    );

    const nameInput = await screen.findByDisplayValue('Alpha');
    fireEvent.change(nameInput, { target: { value: 'Beta' } });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to update sector/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to update sector/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('does not leak raw transport text when planet detail fetch fails', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/planet')) {
        throw new Error('Network Error');
      }
      if (url.endsWith('/port')) {
        return { data: { has_port: false, port: null } };
      }
      throw new Error(`unexpected GET ${url}`);
    });

    render(
      <MemoryRouter>
        <SectorEditModal
          isOpen
          sector={sector as any}
          onClose={() => {}}
          onSave={() => {}}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.queryByText(/Loading planet details/i)).not.toBeInTheDocument();
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
    expect(text).not.toMatch(/TypeError/i);
  });

  it('does not leak TypeError Failed to fetch when port detail fetch fails', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.endsWith('/planet')) {
        return { data: { has_planet: false, planet: null } };
      }
      if (url.endsWith('/port')) {
        throw new TypeError('Failed to fetch');
      }
      throw new Error(`unexpected GET ${url}`);
    });

    render(
      <MemoryRouter>
        <SectorEditModal
          isOpen
          sector={sector as any}
          onClose={() => {}}
          onSave={() => {}}
        />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.queryByText(/Loading port details/i)).not.toBeInTheDocument();
    });

    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
