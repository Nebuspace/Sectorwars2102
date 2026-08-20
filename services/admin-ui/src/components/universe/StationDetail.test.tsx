import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import PortDetail from './StationDetail';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    patch: vi.fn(),
  },
}));

const port = {
  id: 'port-1',
  name: 'Dock',
  port_class: 1,
  type: 'TRADING',
};

describe('StationDetail (PortDetail) scope errors (LEG-1213)', () => {
  beforeEach(() => {
    vi.mocked(api.patch).mockReset();
    vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  it('alerts admin.universe.manage on update 403', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 403'), { response: { status: 403, data: {} } }),
    );

    render(<PortDetail port={port} onBack={() => {}} />);
    fireEvent.click(screen.getByText('Dock'));
    const input = await screen.findByDisplayValue('Dock');
    fireEvent.change(input, { target: { value: 'Dock2' } });
    fireEvent.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(
        expect.stringMatching(/admin\.universe\.manage|Access denied/i),
      );
    });
  });

  it('alerts rate-limit on update 429', async () => {
    vi.mocked(api.patch).mockRejectedValue(
      Object.assign(new Error('HTTP 429'), { response: { status: 429, data: {} } }),
    );

    render(<PortDetail port={port} onBack={() => {}} />);
    fireEvent.click(screen.getByText('Dock'));
    const input = await screen.findByDisplayValue('Dock');
    fireEvent.change(input, { target: { value: 'Dock2' } });
    fireEvent.click(screen.getByRole('button', { name: '✓' }));

    await waitFor(() => {
      expect(window.alert).toHaveBeenCalledWith(expect.stringMatching(/rate limit/i));
    });
  });
});
