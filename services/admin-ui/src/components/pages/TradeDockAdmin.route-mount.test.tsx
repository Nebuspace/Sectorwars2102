import { describe, it, expect, vi, beforeEach } from 'vitest';
import { Suspense } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import TradeDockAdmin from './TradeDockAdmin';
import { api } from '../../utils/auth';

/**
 * LEG-71 — prove TradeDockAdmin renders when navigated via the shell
 * route path (`/tradedocks`), not only via a direct unmounted render().
 * App.tsx + Sidebar wiring landed with LEG-41 on this branch; route-smoke
 * e2e also pins the path + page title.
 */

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: null, logout: vi.fn() }),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => vi.fn(async () => true),
}));

describe('TradeDockAdmin shell route path (LEG-71)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.get).mockResolvedValue({ data: { tradedocks: [] } });
  });

  it('renders page title when navigated to /tradedocks', async () => {
    render(
      <MemoryRouter initialEntries={['/tradedocks']}>
        <Routes>
          <Route
            path="tradedocks"
            element={
              <Suspense fallback={<div>loading</div>}>
                <TradeDockAdmin />
              </Suspense>
            }
          />
        </Routes>
      </MemoryRouter>
    );

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: 'TradeDock management' })
      ).toBeInTheDocument();
    });
    expect(api.get).toHaveBeenCalledWith(
      '/api/v1/admin/construction/tradedocks'
    );
  });
});
