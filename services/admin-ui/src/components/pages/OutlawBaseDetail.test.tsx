import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import OutlawBaseDetail from './OutlawBaseDetail';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

const BASE_ID = '11111111-1111-1111-1111-111111111111';

const payload = {
  id: BASE_ID,
  name: 'Crimson Nest',
  sector_id: 42,
  home_region_id: '22222222-2222-2222-2222-222222222222',
  faction_code: 'OUTLAW',
  archetype: 'raid_den',
  capacity: 8,
  current_occupants_count: 3,
  is_player_discoverable: true,
  raid_cooldown_until: null,
  last_raided_at: null,
  relocation_pending: false,
};

function renderAt(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/outlaw-bases/${id}`]}>
      <Routes>
        <Route path="/outlaw-bases/:id" element={<OutlawBaseDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('OutlawBaseDetail (LEG-4212)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('loads GET outlaw-bases/{id} and displays committed fields', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload });

    renderAt(BASE_ID);

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(`/api/v1/admin/outlaw-bases/${BASE_ID}`);
    });

    expect(await screen.findByTestId('outlaw-base-detail')).toBeTruthy();
    expect(screen.getByTestId('outlaw-base-field-id')).toHaveTextContent(BASE_ID);
    expect(screen.getByTestId('outlaw-base-field-name')).toHaveTextContent('Crimson Nest');
    expect(screen.getByTestId('outlaw-base-field-sector_id')).toHaveTextContent('42');
    expect(screen.getByTestId('outlaw-base-field-faction_code')).toHaveTextContent('OUTLAW');
    expect(screen.getByTestId('outlaw-base-field-raid_cooldown_until')).toHaveTextContent('—');
    expect(screen.queryByText(/parent_holding/i)).toBeNull();
    expect(screen.queryByText(/composition_profile/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /raid|capture|create|save/i })).toBeNull();
  });

  it('surfaces 404 via formatAdminApiError notFoundMessage', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 404'), { response: { status: 404 } }),
    );

    renderAt(BASE_ID);

    const alert = await screen.findByTestId('outlaw-base-error');
    expect(alert.textContent ?? '').toMatch(/OutlawBase not found/i);
    expect(screen.queryByTestId('outlaw-base-fields')).toBeNull();
  });

  it('surfaces generic load failure without inventing fields', async () => {
    vi.mocked(api.get).mockRejectedValue(
      Object.assign(new Error('HTTP 500'), { response: { status: 500 } }),
    );

    renderAt(BASE_ID);

    const alert = await screen.findByTestId('outlaw-base-error');
    expect(alert.textContent ?? '').toMatch(/Failed to load OutlawBase/i);
    expect(screen.queryByTestId('outlaw-base-field-name')).toBeNull();
  });
});
