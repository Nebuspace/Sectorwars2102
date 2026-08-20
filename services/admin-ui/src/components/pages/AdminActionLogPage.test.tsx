import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AdminActionLogPage from './AdminActionLogPage';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

function renderLog() {
  return render(
    <MemoryRouter>
      <AdminActionLogPage />
    </MemoryRouter>,
  );
}

describe('AdminActionLogPage scope errors (LEG-1039)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 ledger load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.audit.view'),
    );

    renderLog();

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.audit\.view/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 ledger load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    renderLog();

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});
