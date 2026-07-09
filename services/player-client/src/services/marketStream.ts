/**
 * Real-time market price stream — dedicated client for the public,
 * read-only `/ws/market-stream` endpoint (services/gameserver/src/api/
 * routes/enhanced_websocket.py, public_market_stream). This is a SEPARATE
 * socket from the general multiplexed connection websocketService owns
 * (`/api/v1/ws/connect`) — market-stream is a purpose-built,
 * commodities-scoped feed with its own message shapes and no post-connect
 * resubscribe, so it gets its own connection rather than being bolted onto
 * the general bus.
 *
 * WO-RT-MARKET-STREAM-CLIENT.
 */
import { getAccessToken, refreshAccessToken } from './apiClient';

/** Unwrapped payload shape under a market_update's "data" key (server-side
 *  unwrap: _unwrap_pubsub_envelope). Two live publishers feed the same
 *  market:{commodity} channels with different shapes — the trade-driven
 *  publish_trade_tick actually wired to the buy/sell routes (routes/
 *  trading.py:_publish_trade_tick) sends {commodity, station_id, buy_price,
 *  sell_price, quantity, current_price, last_transaction}, while
 *  RealTimeMarketService.publish_market_update sends a broader
 *  MarketSnapshot. Every field but `commodity` is therefore optional here;
 *  consumers must treat a missing field as "unchanged", never as zero. */
export interface MarketStreamUpdateData {
  commodity?: string;
  station_id?: string;
  buy_price?: number;
  sell_price?: number;
  quantity?: number;
  current_price?: number;
  last_transaction?: string;
  [key: string]: unknown;
}

export interface MarketStreamUpdateMessage {
  type: 'market_update';
  commodity: string;
  data: MarketStreamUpdateData;
  timestamp: string;
}

export interface MarketStreamConnectionMessage {
  type: 'connection_established';
  commodities: string[];
  update_interval: number;
  timestamp: string;
}

export interface MarketStreamErrorMessage {
  type: 'error';
  message: string;
}

type MarketStreamMessage =
  | MarketStreamUpdateMessage
  | MarketStreamConnectionMessage
  | MarketStreamErrorMessage;

type UpdateHandler = (message: MarketStreamUpdateMessage) => void;
type StatusHandler = (connected: boolean) => void;

export class MarketStreamService {
  private ws: WebSocket | null = null;
  private commodities: string[] = [];
  private shouldReconnect = false;
  private reconnectAttempts = 0;
  private readonly maxReconnectAttempts = 5;
  // Mirrors websocketService's backoff (services/websocket.ts): 1s, doubling
  // to a 30s cap.
  private reconnectDelay = 1000;
  private reconnectTimer: number | null = null;
  // Whether the CURRENT socket attempt ever reached onopen — same auth-vs-
  // transport disambiguation as websocketService (see its `hadOpen` doc).
  private hadOpen = false;
  private refreshingAuth = false;
  private didAuthRefresh = false;
  private connected = false;
  private updateHandlers: Set<UpdateHandler> = new Set();
  private statusHandlers: Set<StatusHandler> = new Set();

  private getStreamUrl(token: string): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

    let base: string;
    if (window.location.host.includes('.app.github.dev')) {
      const gameserverHost = window.location.host.replace('-3000.app.github.dev', '-8080.app.github.dev');
      base = `${protocol}//${gameserverHost}/api/v1/ws/market-stream`;
    } else {
      const apiUrl = import.meta.env.VITE_API_URL;
      base = apiUrl
        ? `${apiUrl.replace(/^http/, 'ws')}/api/v1/ws/market-stream`
        : `${protocol}//${window.location.host}/api/v1/ws/market-stream`;
    }

    const commodityParam = this.commodities.length > 0 ? this.commodities.join(',') : 'ALL';
    return `${base}?token=${encodeURIComponent(token)}&commodities=${encodeURIComponent(commodityParam)}`;
  }

  /** Open a stream scoped to exactly this commodity list. The server has no
   *  post-connect resubscribe — a new commodity set (e.g. docking at a
   *  different port) means a fresh connect() call, which tears down any
   *  existing socket first. */
  connect(commodities: string[]): void {
    if (commodities.length === 0) return;
    this.disconnect(); // always start clean against the new subscription set
    this.commodities = commodities;
    this.shouldReconnect = true;
    this.reconnectAttempts = 0;
    this.reconnectDelay = 1000;
    this.openSocket();
  }

  private openSocket(): void {
    const token = getAccessToken();
    if (!token) {
      console.error('MarketStream: No authentication token available');
      return;
    }

    this.hadOpen = false;
    try {
      this.ws = new WebSocket(this.getStreamUrl(token));
      this.setupHandlers();
    } catch (error) {
      console.error('MarketStream: Failed to create connection', error);
      this.scheduleReconnect();
    }
  }

  private setupHandlers(): void {
    if (!this.ws) return;

    this.ws.onopen = () => {
      this.connected = true;
      this.hadOpen = true;
      this.didAuthRefresh = false;
      this.reconnectAttempts = 0;
      this.reconnectDelay = 1000;
      this.notifyStatus(true);
    };

    this.ws.onmessage = (event) => {
      let message: MarketStreamMessage;
      try {
        message = JSON.parse(event.data);
      } catch (error) {
        console.error('MarketStream: Failed to parse message', error);
        return;
      }

      if (message.type === 'market_update') {
        this.updateHandlers.forEach((handler) => {
          try {
            handler(message as MarketStreamUpdateMessage);
          } catch (error) {
            console.error('MarketStream: Error in update handler', error);
          }
        });
      } else if (message.type === 'error') {
        console.warn('MarketStream: server error', message.message);
      }
      // 'connection_established' is purely informational — no client action needed.
    };

    this.ws.onclose = (event) => {
      this.connected = false;
      this.notifyStatus(false);

      if (!this.shouldReconnect) return;
      // Auth failure surfaces as the server's explicit 4001 (see
      // public_market_stream's close paths) OR a handshake that never
      // opened. A clean drop after a successful open (network blip) just
      // reconnects on plain backoff.
      const authSuspect = event.code === 4001 || !this.hadOpen;
      if (authSuspect && !this.didAuthRefresh) {
        this.reconnectWithRefresh();
      } else {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (error) => {
      console.error('MarketStream: Connection error', error);
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('MarketStream: Max reconnection attempts reached');
      return;
    }

    this.reconnectTimer = window.setTimeout(() => {
      // Re-check at fire time: a disconnect() between scheduling and firing
      // must not resurrect a torn-down subscription.
      if (!this.shouldReconnect) return;
      this.reconnectAttempts++;
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
      this.openSocket();
    }, this.reconnectDelay);
  }

  private reconnectWithRefresh(): void {
    if (this.refreshingAuth) return; // a refresh+reconnect is already in flight
    this.refreshingAuth = true;
    this.didAuthRefresh = true; // one refresh per outage (reset on next open)
    refreshAccessToken()
      .then((newToken) => {
        this.refreshingAuth = false;
        if (!this.shouldReconnect) return;
        if (newToken) {
          this.reconnectAttempts = 0;
          this.reconnectDelay = 1000;
          this.scheduleReconnect();
        } else {
          // Refresh token is also dead — stop retrying a credential that
          // will never work. The REST 401 path owns the login redirect.
          this.shouldReconnect = false;
        }
      })
      .catch(() => {
        this.refreshingAuth = false;
        this.shouldReconnect = false;
      });
  }

  /** Subscribe to live market_update frames. Returns an unsubscribe fn. */
  onUpdate(handler: UpdateHandler): () => void {
    this.updateHandlers.add(handler);
    return () => this.updateHandlers.delete(handler);
  }

  /** Subscribe to connect/disconnect transitions. Returns an unsubscribe fn. */
  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  private notifyStatus(connected: boolean): void {
    this.statusHandlers.forEach((handler) => {
      try {
        handler(connected);
      } catch (error) {
        console.error('MarketStream: Error in status handler', error);
      }
    });
  }

  isConnected(): boolean {
    return this.connected;
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
    this.connected = false;
  }
}

// Export singleton instance — mirrors websocketService's convention.
export const marketStreamService = new MarketStreamService();
export default marketStreamService;
