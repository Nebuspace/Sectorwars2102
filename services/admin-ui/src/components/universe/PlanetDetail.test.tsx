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

const basePlanet = {
  id: 'planet-1',
  name: 'Terra Nova',
  planet_type: 'TERRAN',
  owner_name: 'Ada Colony',
  citadel_level: 1,
  shield_level: 1,
  drones: 10,
  breeding_rate: 5,
  defense_level: 2,
  colonists: { fuel: 100, organics: 50, equipment: 25 },
  production: { ore: 3, organics: 2, equipment: 1 },
};

describe('PlanetDetail Soft-ORDER non-PATCHABLE honesty', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.mocked(api.patch).mockResolvedValue({ data: { success: true } });
  });

  it('non-PATCHABLE owner_name is read-only and does not PATCH', async () => {
    const user = userEvent.setup();
    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);

    const owner = screen.getByText('Ada Colony');
    expect(owner).toHaveAttribute('title', 'Not editable via admin planet PATCH');
    expect(owner.className).toMatch(/read-only/);
    await user.click(owner);
    expect(api.patch).not.toHaveBeenCalled();
  });

  it('name save still PATCHes name', async () => {
    const user = userEvent.setup();
    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);

    const nameLabel = screen.getByText('Name:');
    const row = nameLabel.closest('.info-item');
    const clickable = row!.querySelector('.editable-field.clickable') as HTMLElement;
    await user.click(clickable);

    const input = screen.getByRole('textbox');
    await user.clear(input);
    await user.type(input, 'Renamed World');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/planets/planet-1', {
        name: 'Renamed World',
      });
    });
  });
});
