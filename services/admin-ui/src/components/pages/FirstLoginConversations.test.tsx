import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import FirstLoginConversations from './FirstLoginConversations';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
  },
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

vi.mock('../first-login/ConversationFilters', () => ({
  ConversationFilters: () => null,
}));

vi.mock('../first-login/ConversationTable', () => ({
  ConversationTable: () => null,
}));

vi.mock('../first-login/ConversationDetailModal', () => ({
  ConversationDetailModal: () => null,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

describe('FirstLoginConversations scope errors (LEG-967)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 load', async () => {
    vi.mocked(api.get).mockRejectedValue(
      axiosError(403, 'Missing scope admin.first_login.view'),
    );

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.first_login\.view/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 load', async () => {
    vi.mocked(api.get).mockRejectedValue(axiosError(429));

    render(<FirstLoginConversations />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});
