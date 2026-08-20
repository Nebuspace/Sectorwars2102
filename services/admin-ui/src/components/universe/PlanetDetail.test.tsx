import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PlanetDetail from './PlanetDetail';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    patch: vi.fn(),
  },
}));

vi.mock('../../hooks/useResourceCatalog', () => ({
  useResourceCatalog: () => ({
    getIcon: () => '·',
    getLabel: (k: string) => k,
  }),
}));

const planet = {
  id: 'p1',
  name: 'Terra',
  planet_type: 'TERRAN',
  defense_level: 2,
};

describe('PlanetDetail scope errors (LEG-1214)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  it('alerts admin.universe.manage on patch 403', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403, data: {} } }),
    );

    render(<PlanetDetail planet={planet} onBack={() => {}} />);
    fireEvent.click(screen.getByText('Terra'));
    const input = await screen.findByDisplayValue('Terra');
    fireEvent.change(input, { target: { value: 'Terra2' } });
    fireEvent.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(
        expect.stringMatching(/admin\.universe\.manage|Access denied/i),
      );
    });
  });

  it('alerts rate-limit on patch 429', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429, data: {} } }),
    );

    render(<PlanetDetail planet={planet} onBack={() => {}} />);
    fireEvent.click(screen.getByText('Terra'));
    const input = await screen.findByDisplayValue('Terra');
    fireEvent.change(input, { target: { value: 'Terra2' } });
    fireEvent.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });
});
