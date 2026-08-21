import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

describe('SectorEditModal Soft-ORDER create-port station_class', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { ...sector }, status: 200 });
    vi.mocked(api.post).mockResolvedValue({ data: { id: 'port-1' }, status: 200 });
  });

  it('Create Port POSTs station_class not port_class', async () => {
    const user = userEvent.setup();
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

    const openBtn = await screen.findByRole('button', { name: /^Create Port$/i });
    await user.click(openBtn);

    await user.type(screen.getByPlaceholderText(/port name/i), 'Trade Hub');
    const submitBtns = screen.getAllByRole('button', { name: /^Create Port$/i });
    await user.click(submitBtns[submitBtns.length - 1]);

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe('/api/v1/admin/sectors/sec-1/port');
    expect(body).toMatchObject({
      name: 'Trade Hub',
      station_class: 6,
      type: 'TRADING',
    });
    expect(body).not.toHaveProperty('port_class');
  });
});
