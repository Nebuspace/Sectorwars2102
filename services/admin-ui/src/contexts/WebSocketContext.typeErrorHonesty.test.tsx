import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { WebSocketProvider, useWebSocket } from './WebSocketContext';
import { websocketService } from '../services/websocket';

const mockUseAuth = vi.fn();

vi.mock('./AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('../services/websocket', () => ({
  websocketService: {
    connect: vi.fn(),
    disconnect: vi.fn(),
    isConnected: vi.fn(),
    hasGivenUp: vi.fn(),
    getReconnectAttempt: vi.fn(),
    getMaxReconnectAttempts: vi.fn(),
    onGaveUp: vi.fn(),
    retryConnection: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    send: vi.fn(),
  },
}));

const mockedWs = vi.mocked(websocketService, true);

function assertNoTransportLeak(text: string) {
  expect(text).not.toBe('Network Error');
  expect(text).not.toContain('Network Error');
  expect(text).not.toMatch(/Failed to fetch/i);
  expect(text).not.toMatch(/TypeError/i);
}

function Probe() {
  const {
    isConnected,
    hasGivenUp,
    reconnectAttempt,
    maxReconnectAttempts,
    retryConnection,
  } = useWebSocket();

  return (
    <div>
      <span data-testid="connected">{String(isConnected)}</span>
      <span data-testid="gave-up">{String(hasGivenUp)}</span>
      <span data-testid="attempt">{reconnectAttempt}</span>
      <span data-testid="max-attempts">{maxReconnectAttempts}</span>
      <button type="button" onClick={() => retryConnection()}>
        Retry
      </button>
    </div>
  );
}

/**
 * LEG-3793 Soft-ORDER — WebSocketContext TypeError/Network Error densify.
 */
describe('WebSocketContext typeErrorHonesty densify (LEG-3793)', () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({
      user: { id: '1', username: 'alice' },
      token: 'auth-token',
    });
    mockedWs.connect.mockReset();
    mockedWs.disconnect.mockReset();
    mockedWs.retryConnection.mockReset();
    mockedWs.isConnected.mockReturnValue(false);
    mockedWs.hasGivenUp.mockReturnValue(false);
    mockedWs.getReconnectAttempt.mockReturnValue(0);
    mockedWs.getMaxReconnectAttempts.mockReturnValue(5);
    mockedWs.onGaveUp.mockReturnValue(vi.fn());
    mockedWs.on.mockReturnValue(vi.fn());
    vi.spyOn(console, 'warn').mockImplementation(() => {});
  });

  it('initial connect TypeError does not leak raw transport text in UI state', async () => {
    mockedWs.connect.mockRejectedValue(new TypeError('Failed to fetch'));
    mockedWs.hasGivenUp.mockReturnValue(true);
    mockedWs.getReconnectAttempt.mockReturnValue(5);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId('gave-up')).toHaveTextContent('true'),
    );

    assertNoTransportLeak(document.body.textContent ?? '');
    expect(screen.getByTestId('attempt')).toHaveTextContent('5');
  });

  it('initial connect Network Error does not leak raw transport text in UI state', async () => {
    mockedWs.connect.mockRejectedValue(new Error('Network Error'));
    mockedWs.hasGivenUp.mockReturnValue(true);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId('gave-up')).toHaveTextContent('true'),
    );

    assertNoTransportLeak(document.body.textContent ?? '');
  });

  it('manual retry TypeError does not leak raw transport text in UI state', async () => {
    mockedWs.connect.mockResolvedValue(undefined);
    mockedWs.retryConnection.mockRejectedValue(new TypeError('Failed to fetch'));
    mockedWs.hasGivenUp.mockReturnValue(true);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());

    await act(async () => {
      screen.getByRole('button', { name: 'Retry' }).click();
    });

    await waitFor(() => expect(mockedWs.retryConnection).toHaveBeenCalled());
    expect(screen.getByTestId('gave-up')).toHaveTextContent('true');
    assertNoTransportLeak(document.body.textContent ?? '');
  });

  it('manual retry Network Error does not leak raw transport text in UI state', async () => {
    mockedWs.connect.mockResolvedValue(undefined);
    mockedWs.retryConnection.mockRejectedValue(new Error('Network Error'));
    mockedWs.hasGivenUp.mockReturnValue(true);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());

    await act(async () => {
      screen.getByRole('button', { name: 'Retry' }).click();
    });

    await waitFor(() => expect(mockedWs.retryConnection).toHaveBeenCalled());
    assertNoTransportLeak(document.body.textContent ?? '');
  });
});
