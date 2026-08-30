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

vi.mock('../ui/PageHeader', () => ({
  default: () => null,
}));

const sector = {
  id: 'sec-1',
  sector_id: 1,
  name: 'Alpha',
  type: 'NORMAL',
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

describe('SectorEditModal scope errors (LEG-1213)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.put).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { ...sector }, status: 200 });
  });

  it('surfaces admin.universe.manage on update 403', async () => {
    vi.mocked(api.put).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403, data: {} } }),
    );

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

    // Force a dirty save: change name then click Save if present
    const nameInput = await screen.findByDisplayValue('Alpha');
    fireEvent.change(nameInput, { target: { value: 'Beta' } });
    const saveBtn = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(screen.getByText(/admin\.universe\.manage|Access denied/i)).toBeTruthy();
    });
  });

  it('surfaces rate-limit on update 429', async () => {
    vi.mocked(api.put).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429, data: {} } }),
    );

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
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });

  it('surfaces honest fallback on update TypeError/network collapse (LEG-3066)', async () => {
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
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to update sector/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to update sector/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });
});
