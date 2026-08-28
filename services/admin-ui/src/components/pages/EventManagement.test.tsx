import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import EventManagement from './EventManagement';
import { api } from '../../utils/auth';

vi.mock('../../utils/auth', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
const toastInfo = vi.fn();
const toastWarning = vi.fn();
const confirmMock = vi.fn();

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({
    success: toastSuccess,
    error: toastError,
    info: toastInfo,
    warning: toastWarning,
  }),
  useConfirm: () => confirmMock,
}));

vi.mock('../ui/PageHeader', () => ({
  default: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

const axiosError = (status: number, detail?: string) =>
  Object.assign(new Error(`HTTP ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

const sampleEvent = {
  id: 'evt-1',
  title: 'Test Event',
  description: 'desc',
  event_type: 'economic',
  status: 'scheduled',
  start_time: '2026-09-01T12:00:00Z',
  end_time: '2026-09-02T12:00:00Z',
  affected_regions: [],
  effects: [],
  participation_count: 0,
  rewards_distributed: 0,
  created_by: 'admin',
  created_at: '2026-08-01T00:00:00Z',
};

const emptyStats = {
  total_events: 0,
  active_events: 0,
  scheduled_events: 0,
  total_participants: 0,
  rewards_distributed: 0,
};

const mockLoad = (events = [sampleEvent]) => {
  vi.mocked(api.get).mockImplementation(async (url: string) => {
    if (url.includes('/events/templates')) {
      return { data: [] };
    }
    if (url.includes('/events/stats')) {
      return { data: emptyStats };
    }
    if (url.includes('/events/')) {
      return { data: { events, total_pages: 1 } };
    }
    return { data: {} };
  });
};

describe('EventManagement scope errors (LEG-967)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    toastInfo.mockReset();
    toastWarning.mockReset();
    confirmMock.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 primary load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/')) {
        throw axiosError(403, 'Missing scope admin.events.manage');
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.events\.manage/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 primary load', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/')) {
        throw axiosError(429);
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

describe('EventManagement stats load errors (LEG-2685)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    toastInfo.mockReset();
    toastWarning.mockReset();
    confirmMock.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 stats load when events list succeeds', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/stats')) {
        throw axiosError(403, 'Missing scope admin.events.manage');
      }
      if (url.includes('/events/')) {
        return { data: { events: [sampleEvent], total_pages: 1 } };
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/Missing scope admin\.events\.manage/i)).toBeTruthy();
    });
    expect(screen.queryByText(/^Failed to fetch event data$/)).toBeNull();
  });

  it('shows rate-limit copy on 429 stats load when events list succeeds', async () => {
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        return { data: [] };
      }
      if (url.includes('/events/stats')) {
        throw axiosError(429);
      }
      if (url.includes('/events/')) {
        return { data: { events: [sampleEvent], total_pages: 1 } };
      }
      return { data: {} };
    });

    render(<EventManagement />);

    await waitFor(() => {
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
    expect(screen.queryByText(/^Failed to fetch event data$/)).toBeNull();
  });
});

describe('EventManagement templates load errors (LEG-2686)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    toastInfo.mockReset();
    toastWarning.mockReset();
    confirmMock.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces scope denial on 403 templates load in create form', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        throw axiosError(403, 'Missing scope admin.events.manage');
      }
      if (url.includes('/events/stats')) {
        return { data: emptyStats };
      }
      if (url.includes('/events/')) {
        return { data: { events: [sampleEvent], total_pages: 1 } };
      }
      return { data: {} };
    });

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Create Event' }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Create New Event' })).toBeTruthy();
    });

    await waitFor(() => {
      expect(screen.getByText(/Unable to load event templates:/i)).toBeTruthy();
      expect(screen.getByText(/Missing scope admin\.events\.manage/i)).toBeTruthy();
    });
  });

  it('shows rate-limit copy on 429 templates load in create form', async () => {
    const user = userEvent.setup();
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url.includes('/events/templates')) {
        throw axiosError(429);
      }
      if (url.includes('/events/stats')) {
        return { data: emptyStats };
      }
      if (url.includes('/events/')) {
        return { data: { events: [sampleEvent], total_pages: 1 } };
      }
      return { data: {} };
    });

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Create Event' }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Create New Event' })).toBeTruthy();
    });

    await waitFor(() => {
      expect(screen.getByText(/Unable to load event templates:/i)).toBeTruthy();
      expect(screen.getByText(/rate limit/i)).toBeTruthy();
    });
  });
});

describe('EventManagement mutation scope errors (LEG-2597)', () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    toastSuccess.mockReset();
    toastError.mockReset();
    toastInfo.mockReset();
    toastWarning.mockReset();
    confirmMock.mockReset();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  it('surfaces formatAdminApiError on create 403', async () => {
    const user = userEvent.setup();
    mockLoad();
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.events.manage'),
    );

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Create Event' }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Create New Event' })).toBeTruthy();
    });
    await user.click(screen.getByRole('button', { name: 'Create Event' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/events/',
        expect.any(Object),
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.events.manage');
    expect(toastError).not.toHaveBeenCalledWith('Error creating event');
  });

  it('surfaces rate-limit copy on create 429', async () => {
    const user = userEvent.setup();
    mockLoad();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Create Event' }));
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Create New Event' })).toBeTruthy();
    });
    await user.click(screen.getByRole('button', { name: 'Create Event' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/events/',
        expect.any(Object),
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Error creating event');
  });

  it('surfaces formatAdminApiError on cancel 403', async () => {
    const user = userEvent.setup();
    mockLoad();
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.events.manage'),
    );

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/events/evt-1/deactivate',
      );
    });
    expect(confirmMock).toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.events.manage');
    expect(toastError).not.toHaveBeenCalledWith('Error cancelling event');
  });

  it('surfaces rate-limit copy on cancel 429', async () => {
    const user = userEvent.setup();
    mockLoad();
    confirmMock.mockResolvedValue(true);
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/events/evt-1/deactivate',
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Error cancelling event');
  });

  it('surfaces formatAdminApiError on activate 403', async () => {
    const user = userEvent.setup();
    mockLoad();
    vi.mocked(api.post).mockRejectedValue(
      axiosError(403, 'Missing scope admin.events.manage'),
    );

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Activate' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/events/evt-1/activate',
      );
    });
    expect(toastError).toHaveBeenCalledWith('Missing scope admin.events.manage');
    expect(toastError).not.toHaveBeenCalledWith('Error activating event');
  });

  it('surfaces rate-limit copy on activate 429', async () => {
    const user = userEvent.setup();
    mockLoad();
    vi.mocked(api.post).mockRejectedValue(axiosError(429));

    render(<EventManagement />);
    await waitFor(() => expect(screen.getByText('Test Event')).toBeTruthy());

    await user.click(screen.getByRole('button', { name: 'Activate' }));

    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith(
        '/api/v1/admin/events/evt-1/activate',
      );
    });
    expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/rate limit/i),
    );
    expect(toastError).not.toHaveBeenCalledWith('Error activating event');
  });
});
