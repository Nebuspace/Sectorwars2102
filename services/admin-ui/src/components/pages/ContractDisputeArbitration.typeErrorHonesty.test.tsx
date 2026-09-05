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

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

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

/**
 * LEG-3667 Soft-ORDER — ContractDisputeArbitration TypeError/Network Error densify.
 */
describe('ContractDisputeArbitration typeErrorHonesty densify (LEG-3667)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('collapses axios Network Error on disputes load without leaking raw transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new Error('Network Error'));

    render(<ContractDisputeArbitration />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load disputed contracts/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load disputed contracts/i).textContent ?? '';
    expect(text).not.toBe('Network Error');
    expect(text).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on disputes load without leaking transport text', async () => {
    vi.mocked(api.get).mockRejectedValue(new TypeError('Failed to fetch'));

    render(<ContractDisputeArbitration />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load disputed contracts/i)).toBeTruthy();
    });
    const text = screen.getByText(/Failed to load disputed contracts/i).textContent ?? '';
    expect(text).not.toMatch(/Failed to fetch/i);
    expect(text).not.toMatch(/TypeError/i);
  });

  it('collapses axios Network Error on resolve POST without leaking raw transport text', async () => {
    mockDisputesLoaded();
    vi.mocked(api.post).mockRejectedValue(new Error('Network Error'));

    await openRulingForm();
    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      const resolveError = document.querySelector('.resolve-error');
      expect(resolveError).toBeTruthy();
      expect(resolveError?.textContent).toMatch(/Failed to resolve dispute/i);
    });

    const msg = document.querySelector('.resolve-error')?.textContent ?? '';
    expect(msg).not.toBe('Network Error');
    expect(msg).not.toContain('Network Error');
  });

  it('collapses TypeError Failed to fetch on resolve POST without leaking transport text', async () => {
    mockDisputesLoaded();
    vi.mocked(api.post).mockRejectedValue(new TypeError('Failed to fetch'));

    await openRulingForm();
    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      const resolveError = document.querySelector('.resolve-error');
      expect(resolveError).toBeTruthy();
      expect(resolveError?.textContent).toMatch(/Failed to resolve dispute/i);
    });

    const msg = document.querySelector('.resolve-error')?.textContent ?? '';
    expect(msg).not.toMatch(/Failed to fetch/i);
    expect(msg).not.toMatch(/TypeError/i);
  });

  it('surfaces 403 with scope-aware copy on escalated disputes GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(403));

    render(<ContractDisputeArbitration />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to load disputed contracts|Access denied/i)).toBeTruthy();
    });

    const text =
      document.querySelector('.alert-message')?.textContent ??
      screen.getByText(/Access denied|disputed contracts/i).textContent ??
      '';
    expect(text).toMatch(/Access denied|contract dispute arbitration scopes/i);
    expect(text).not.toMatch(/HTTP 403/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 429 as admin rate-limit copy on escalated disputes GET', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<ContractDisputeArbitration />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });

    const text = document.querySelector('.alert-message')?.textContent ?? '';
    expect(text).toMatch(/rate limit/i);
    expect(text).not.toMatch(/HTTP 429/i);
    assertNoTransportLeak(text);
  });

  it('surfaces 403 with formatAdminApiError-friendly copy on ruling POST', async () => {
    mockDisputesLoaded();
    vi.mocked(api.post).mockRejectedValue(axiosError(403));

    await openRulingForm();
    fireEvent.click(screen.getByRole('button', { name: 'Submit Ruling' }));

    await waitFor(() => {
      const resolveError = document.querySelector('.resolve-error');
      expect(resolveError).toBeTruthy();
      expect(resolveError?.textContent).toMatch(/Access denied|contract dispute arbitration scopes/i);
    });

    const msg = document.querySelector('.resolve-error')?.textContent ?? '';
    expect(msg).not.toMatch(/HTTP 403/i);
    expect(msg).not.toBe('Request failed with status code 403');
    assertNoTransportLeak(msg);
  });
});
