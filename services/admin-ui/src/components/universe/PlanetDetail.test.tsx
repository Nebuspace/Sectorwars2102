import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import PlanetDetail, { buildPlanetPatchPayload } from './PlanetDetail';
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

function httpErr(status: number, detail?: string) {
  return Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });
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

describe('buildPlanetPatchPayload (Soft-ORDER honesty)', () => {
  it('maps planet_type → type', () => {
    expect(buildPlanetPatchPayload('planet_type', 'DESERT')).toEqual({ type: 'DESERT' });
  });

  it('maps owner_id — empty clears to null; never owner_name', () => {
    expect(
      buildPlanetPatchPayload('owner_id', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
    ).toEqual({ owner_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' });
    expect(buildPlanetPatchPayload('owner_id', '  ')).toEqual({ owner_id: null });
  });

  it('maps residual PlanetUpdateRequest schema fields (LEG-1489)', () => {
    expect(buildPlanetPatchPayload('size', 5)).toEqual({ size: 5 });
    expect(buildPlanetPatchPayload('position', 3)).toEqual({ position: 3 });
    expect(buildPlanetPatchPayload('gravity', 1.25)).toEqual({ gravity: 1.25 });
    expect(buildPlanetPatchPayload('habitability_score', 80)).toEqual({
      habitability_score: 80,
    });
    expect(buildPlanetPatchPayload('resource_richness', 2.5)).toEqual({
      resource_richness: 2.5,
    });
  });
});

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

  it('saving owner sends owner_id not owner_name', async () => {
    const user = userEvent.setup();
    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);

    const ownerLabel = screen.getByText('Owner ID:');
    const row = ownerLabel.closest('.info-item');
    const clickable = row!.querySelector('.editable-field.clickable') as HTMLElement;
    await user.click(clickable);

    const input = screen.getByRole('textbox');
    await user.clear(input);
    await user.type(input, '22222222-2222-2222-2222-222222222222');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/planets/planet-1', {
        owner_id: '22222222-2222-2222-2222-222222222222',
      });
    });
    expect(JSON.stringify(vi.mocked(api.patch).mock.calls[0][1])).not.toMatch(/owner_name/);
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

  it('saving size / gravity / habitability_score posts PlanetUpdateRequest keys (LEG-1489)', async () => {
    const user = userEvent.setup();
    render(
      <PlanetDetail
        planet={{
          ...basePlanet,
          size: 2,
          gravity: 1,
          habitability_score: 40,
        }}
        onBack={() => undefined}
      />,
    );

    const sizeLabel = screen.getByText('Size:');
    const sizeRow = sizeLabel.closest('.info-item');
    await user.click(sizeRow!.querySelector('.editable-field.clickable') as HTMLElement);
    const sizeInput = screen.getByRole('spinbutton');
    fireEvent.change(sizeInput, { target: { value: '7' } });
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/planets/planet-1', { size: 7 });
    });

    vi.mocked(api.patch).mockClear();
    const gravLabel = screen.getByText('Gravity:');
    const gravRow = gravLabel.closest('.info-item');
    await user.click(gravRow!.querySelector('.editable-field.clickable') as HTMLElement);
    const gravInput = screen.getByRole('spinbutton');
    fireEvent.change(gravInput, { target: { value: '1.8' } });
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/planets/planet-1', {
        gravity: 1.8,
      });
    });

    vi.mocked(api.patch).mockClear();
    const habLabel = screen.getByText('Habitability:');
    const habRow = habLabel.closest('.info-item');
    await user.click(habRow!.querySelector('.editable-field.clickable') as HTMLElement);
    const habInput = screen.getByRole('spinbutton');
    fireEvent.change(habInput, { target: { value: '95' } });
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/planets/planet-1', {
        habitability_score: 95,
      });
    });

    const lastBodies = vi.mocked(api.patch).mock.calls.map((c) => JSON.stringify(c[1]));
    expect(lastBodies.join('|')).not.toMatch(/owner_name|drones|colonists/);
  });
});

describe('PlanetDetail PATCH errors (LEG-2616)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
  });

  it('surfaces formatAdminApiError on name PATCH 403', async () => {
    const user = userEvent.setup();
    vi.mocked(api.patch).mockRejectedValue(
      httpErr(403, 'Missing scope admin.universe.manage'),
    );

    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);

    const nameLabel = screen.getByText('Name:');
    const row = nameLabel.closest('.info-item');
    await user.click(row!.querySelector('.editable-field.clickable') as HTMLElement);

    const input = screen.getByRole('textbox');
    await user.clear(input);
    await user.type(input, 'Forbidden Rename');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/planets/planet-1', {
        name: 'Forbidden Rename',
      });
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(
        /Missing scope admin\.universe\.manage/i,
      );
    });
  });

  it('surfaces rate-limit copy on name PATCH 429', async () => {
    const user = userEvent.setup();
    vi.mocked(api.patch).mockRejectedValue(httpErr(429));

    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);

    const nameLabel = screen.getByText('Name:');
    const row = nameLabel.closest('.info-item');
    await user.click(row!.querySelector('.editable-field.clickable') as HTMLElement);

    const input = screen.getByRole('textbox');
    await user.clear(input);
    await user.type(input, 'Rate Limited');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/rate limit/i);
    });
  });

  it('surfaces honest fallback on name PATCH TypeError/network collapse (LEG-2994)', async () => {
    const user = userEvent.setup();
    vi.mocked(api.patch).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<PlanetDetail planet={basePlanet} onBack={() => undefined} />);

    const nameLabel = screen.getByText('Name:');
    const row = nameLabel.closest('.info-item');
    await user.click(row!.querySelector('.editable-field.clickable') as HTMLElement);

    const input = screen.getByRole('textbox');
    await user.clear(input);
    await user.type(input, 'Network Collapse');
    await user.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(api.patch).toHaveBeenCalledWith('/api/v1/admin/planets/planet-1', {
        name: 'Network Collapse',
      });
    });
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toMatch(/Failed to update name/i);
    });

    const msg = screen.getByRole('alert').textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });
});
