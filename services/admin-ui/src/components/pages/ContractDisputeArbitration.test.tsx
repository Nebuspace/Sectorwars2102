import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { ContractDisputeArbitration } from './ContractDisputeArbitration';
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

const sampleDispute = {
  id: 'contract-1',
  payment: 1000,
  penalty: 100,
  dispute_notes: 'Late delivery',
  dispute_filed_at: '2026-01-01T00:00:00Z',
  deadline: '2026-02-01T00:00:00Z',
  commodity_type: 'ore',
  quantity: 10,
  acceptor_player_id: 'player-1',
  issuer_type: 'corp',
  issuer_id: 'corp-1',
  escalated_to_admin: true,
  contract_type: 'escort',
  status: 'disputed',
};

function mockDisputesLoaded() {
  vi.mocked(api.get).mockResolvedValue({ data: [sampleDispute] });
}

async function openRulingForm() {
  render(<ContractDisputeArbitration />);
  await waitFor(() => {
    expect(screen.getByText(/Escalated Queue \(1\)/i)).toBeTruthy();
  });
  fireEvent.click(screen.getByText(/ore x10/i));
  fireEvent.click(screen.getByRole('button', { name: 'Full Payout' }));
}

describe('ContractDisputeArbitration scope errors (LEG-968)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.contracts.disputes'),
    );

    render(<ContractDisputeArbitration />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.contracts\.disputes/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<ContractDisputeArbitration />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

describe('ContractDisputeArbitration resolve mutation errors (LEG-2625)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    mockDisputesLoaded();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces formatAdminApiError on resolve POST 403', async () => {
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.contracts.disputes.resolve'),
    );

    await openRulingForm();
    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/api/v1/admin/contracts/${sampleDispute.id}/resolve-dispute`,
        expect.objectContaining({ outcome: 'full_payout' }),
      );
    });

    await waitFor(() => {
      const resolveError = document.querySelector('.resolve-error');
      expect(resolveError).toBeTruthy();
      expect(resolveError?.textContent).toMatch(/Missing scope admin\.contracts\.disputes\.resolve/i);
      expect(resolveError?.textContent).not.toMatch(/^Failed to resolve dispute$/);
    });
  });

  it('shows rate-limit copy on resolve POST 429', async () => {
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    await openRulingForm();
    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalled();
    });

    await waitFor(() => {
      const resolveError = document.querySelector('.resolve-error');
      expect(resolveError).toBeTruthy();
      expect(resolveError?.textContent).toMatch(/rate limit/i);
      expect(resolveError?.textContent).not.toMatch(/^Failed to resolve dispute$/);
    });
  });

  it('surfaces honest fallback on resolve POST TypeError/network collapse (LEG-2984)', async () => {
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    await openRulingForm();
    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        `/api/v1/admin/contracts/${sampleDispute.id}/resolve-dispute`,
        expect.objectContaining({ outcome: 'full_payout' }),
      );
    });

    await waitFor(() => {
      const resolveError = document.querySelector('.resolve-error');
      expect(resolveError).toBeTruthy();
      expect(resolveError?.textContent).toMatch(/Failed to resolve dispute/i);
    });

    const msg = document.querySelector('.resolve-error')?.textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });
});
