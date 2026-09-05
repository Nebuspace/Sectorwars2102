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

describe('WebSocketContext / WebSocketProvider (LEG-3171)', () => {
  let gaveUpHandler: (() => void) | null = null;

  beforeEach(() => {
    gaveUpHandler = null;
    mockUseAuth.mockReturnValue({ user: null, token: null });
    mockedWs.connect.mockReset();
    mockedWs.disconnect.mockReset();
    mockedWs.isConnected.mockReturnValue(false);
    mockedWs.hasGivenUp.mockReturnValue(false);
    mockedWs.getReconnectAttempt.mockReturnValue(0);
    mockedWs.getMaxReconnectAttempts.mockReturnValue(5);
    mockedWs.onGaveUp.mockImplementation((cb: () => void) => {
      gaveUpHandler = cb;
      return vi.fn();
    });
    mockedWs.retryConnection.mockResolvedValue(undefined);
    mockedWs.on.mockReturnValue(vi.fn());
  });

  it('connects when user and token are present', async () => {
    mockUseAuth.mockReturnValue({
      user: { id: '1', username: 'alice' },
      token: 'auth-token',
    });
    mockedWs.connect.mockResolvedValue(undefined);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => {
      expect(mockedWs.connect).toHaveBeenCalledWith('auth-token');
    });
    await waitFor(() => {
      expect(screen.getByTestId('connected')).toHaveTextContent('true');
    });
    expect(mockedWs.onGaveUp).toHaveBeenCalled();
  });

  it('syncs connection state when the initial connect rejects', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mockUseAuth.mockReturnValue({
      user: { id: '1', username: 'alice' },
      token: 'auth-token',
    });
    mockedWs.connect.mockRejectedValue(new Error('offline'));
    mockedWs.hasGivenUp.mockReturnValue(true);
    mockedWs.getReconnectAttempt.mockReturnValue(5);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByTestId('gave-up')).toHaveTextContent('true'));
    expect(screen.getByTestId('attempt')).toHaveTextContent('5');
    expect(warnSpy).toHaveBeenCalledWith(
      'WebSocket connection unavailable:',
      expect.any(Error)
    );
    warnSpy.mockRestore();
  });

  it('syncs gave-up state when the websocket service abandons reconnect', async () => {
    mockUseAuth.mockReturnValue({
      user: { id: '1', username: 'alice' },
      token: 'auth-token',
    });
    mockedWs.connect.mockResolvedValue(undefined);
    mockedWs.getReconnectAttempt.mockReturnValue(5);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => expect(mockedWs.onGaveUp).toHaveBeenCalled());

    act(() => {
      gaveUpHandler?.();
    });

    expect(screen.getByTestId('gave-up')).toHaveTextContent('true');
    expect(screen.getByTestId('attempt')).toHaveTextContent('5');
  });

  it('retryConnection invokes the service and refreshes connection state', async () => {
    mockUseAuth.mockReturnValue({
      user: { id: '1', username: 'alice' },
      token: 'auth-token',
    });
    mockedWs.connect.mockResolvedValue(undefined);
    mockedWs.hasGivenUp.mockReturnValue(true);
    mockedWs.isConnected.mockReturnValue(true);
    mockedWs.getReconnectAttempt.mockReturnValue(0);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());

    act(() => {
      gaveUpHandler?.();
    });
    expect(screen.getByTestId('gave-up')).toHaveTextContent('true');

    mockedWs.hasGivenUp.mockReturnValue(false);
    await act(async () => {
      screen.getByRole('button', { name: 'Retry' }).click();
    });

    expect(mockedWs.retryConnection).toHaveBeenCalled();
    expect(screen.getByTestId('gave-up')).toHaveTextContent('false');
    expect(screen.getByTestId('connected')).toHaveTextContent('true');
  });

  it('syncs state when manual retry fails', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    mockUseAuth.mockReturnValue({
      user: { id: '1', username: 'alice' },
      token: 'auth-token',
    });
    mockedWs.connect.mockResolvedValue(undefined);
    mockedWs.retryConnection.mockRejectedValue(new Error('still down'));
    mockedWs.hasGivenUp.mockReturnValue(true);

    render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());

    await act(async () => {
      screen.getByRole('button', { name: 'Retry' }).click();
    });

    expect(screen.getByTestId('gave-up')).toHaveTextContent('true');
    expect(warnSpy).toHaveBeenCalledWith(
      'WebSocket manual retry failed:',
      expect.any(Error)
    );
    warnSpy.mockRestore();
  });

  it('disconnects when user logs out', async () => {
    mockUseAuth.mockReturnValue({
      user: { id: '1', username: 'alice' },
      token: 'auth-token',
    });
    mockedWs.connect.mockResolvedValue(undefined);

    const { rerender } = render(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    await waitFor(() => expect(mockedWs.connect).toHaveBeenCalled());

    mockUseAuth.mockReturnValue({ user: null, token: null });
    rerender(
      <WebSocketProvider>
        <Probe />
      </WebSocketProvider>,
    );

    expect(mockedWs.disconnect).toHaveBeenCalled();
    expect(screen.getByTestId('connected')).toHaveTextContent('false');
    expect(screen.getByTestId('gave-up')).toHaveTextContent('false');
  });
});
