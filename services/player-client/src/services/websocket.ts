import { refreshAccessToken, getAccessToken } from './apiClient';

export interface WebSocketMessage {
  type: string;
  [key: string]: any;
}

export interface ChatMessage {
  type: 'chat_message';
  from_user_id: string;
  from_username: string;
  content: string;
  target_type: 'sector' | 'team' | 'global';
  timestamp: string;
  sector_id?: number;
  team_id?: string;
}

export interface PlayerMovementMessage {
  type: 'player_entered_sector' | 'player_left_sector';
  user_id: string;
  username: string;
  sector_id: number;
  timestamp: string;
}

export interface SectorPlayersMessage {
  type: 'sector_players';
  sector_id: number;
  players: Array<{
    user_id: string;
    username: string;
    connected_at: string;
    last_heartbeat: string;
  }>;
  timestamp: string;
}

export interface NotificationMessage {
  type: 'notification';
  title: string;
  content: string;
  level: 'info' | 'success' | 'warning' | 'error';
  timestamp: string;
}

export interface MedalAwardedMessage {
  type: 'medal_awarded';
  medal_id: string;
  medal_name: string | null;
  medal_category: string | null;
  medal_tier: string | null;
  medal_description: string | null;
  medal_icon: string | null;
  awarded_via: string;
  timestamp: string;
}

export interface ARIAChatMessage {
  type: 'aria_chat';
  content: string;
  conversation_id?: string;
  context?: string;
  timestamp: string;
  session_id: string;
  signature?: string;
}

export interface ARIAResponseMessage {
  type: 'aria_response';
  conversation_id: string;
  data: {
    message: string;
    confidence: number;
    context_used: string;
    actions: Array<{
      type: string;
      [key: string]: any;
    }>;
    suggestions: string[];
    learning_note?: string;
  };
  timestamp: string;
  server_version: string;
  signature?: string;
}

// ARIA narration push (WO-ARIA-NARRATE-KERNEL / ADR-0068). Server-side
// delivery (the drain_due_lines → WS push wiring) lands in a later WO --
// this handler is the client-side seam, ready to activate the moment the
// server starts emitting the type. Shape matches
// aria_narration_service.NarrationLine.to_payload() exactly.
export interface ARIANarrationMessage {
  type: 'aria_narration';
  event_id: string;
  line: string;
  priority: number;
  ts: string;
}

// Quantum-shard harvest push (quantum.py's _emit_quantum_harvest, sent to the
// harvesting player's own socket only, post-commit). Canon Resolution step 6:
// "Emit a real-time event on the WebSocket bus so the client UI updates
// without polling". Payload shape is a builder proposal pending DECISIONS
// ratification — consumers should treat every field but `type` as optional.
export interface QuantumHarvestMessage {
  type: 'quantum_harvest';
  sector_id: number;
  nebula_type: string;
  shards: number;
  crit: boolean;
  timestamp: string;
}

// Coarse link-status projection of the reconnect state machine below, for
// chrome that needs "is the uplink healthy" without tracking every close
// code/backoff detail itself (WO-PUX-UPLINK-HUD). 'reconnecting' covers both
// the initial connect and every backoff/refresh retry; 'down' is reserved for
// states where nothing is actively retrying (terminal closes, exhausted
// backoff, or no token).
export type LinkStatus = 'up' | 'reconnecting' | 'down';

export interface LinkStatusMessage {
  type: 'link_status';
  status: LinkStatus;
  timestamp: string;
}

type MessageHandler = (message: WebSocketMessage) => void;

class WebSocketService {
  private ws: WebSocket | null = null;
  private token: string | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000; // Start with 1 second
  private reconnectTimer: number | null = null;
  private heartbeatInterval: ReturnType<typeof setInterval> | null = null;
  private messageHandlers: Set<MessageHandler> = new Set();
  private isConnected = false;
  private shouldReconnect = true;
  // Whether the CURRENT socket attempt ever reached onopen. A close without an
  // open is a handshake/auth rejection (an expired token is rejected pre-accept
  // and surfaces to the browser as code 1006, NOT the server's 4001). A
  // post-accept 4001 is always this socket's own connection having reached
  // onopen first — including the eviction case (reason 'superseded'), which
  // is handled separately from auth failure in the onclose handler below.
  private hadOpen = false;
  // Guards against stacking multiple refresh-then-reconnect cycles at once.
  private refreshingAuth = false;
  // One refresh per outage: if a reconnect still fails AFTER a fresh token, the
  // token isn't the problem (transport), so fall back to plain backoff instead
  // of hammering the refresh endpoint. Reset on a successful open.
  private didAuthRefresh = false;
  // Coarse status for chrome (see LinkStatus above). Starts 'down' — nothing
  // has attempted a connection yet.
  private linkStatus: LinkStatus = 'down';

  constructor() {
    this.setupEventListeners();
  }

  private setupEventListeners() {
    // Handle page visibility changes
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && !this.isConnected && this.token) {
        this.connect();
      }
    });

    // Handle online/offline events
    window.addEventListener('online', () => {
      if (!this.isConnected && this.token) {
        this.connect();
      }
    });

    window.addEventListener('offline', () => {
      this.disconnect();
    });
  }

  private getWebSocketUrl(): string {
    // For Docker environments, always use localhost:8080 for WebSocket
    // This works because the Docker ports are mapped to the host
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    
    // In Docker/Codespaces, use the external port mapping
    if (window.location.host.includes('.app.github.dev')) {
      // GitHub Codespaces detected - using external gameserver WebSocket
      // Use the external gameserver URL for WebSocket
      const gameserverHost = window.location.host.replace('-3000.app.github.dev', '-8080.app.github.dev');
      return `${protocol}//${gameserverHost}/api/v1/ws/connect`;
    }
    
    // Use VITE_API_URL if set, otherwise same-origin — the Vite proxy
    // (ws: true) and the nginx gateway both forward /api/v1/ws upstream
    const apiUrl = import.meta.env.VITE_API_URL;
    if (apiUrl) {
      const wsUrl = apiUrl.replace(/^http/, 'ws');
      return `${wsUrl}/api/v1/ws/connect`;
    }
    return `${protocol}//${window.location.host}/api/v1/ws/connect`;
  }

  /** Public entry (login / fresh session): re-arms auto-reconnect and resets
   *  the backoff, then opens the socket. Reconnect attempts go through
   *  openSocket() so they preserve the backoff counter. */
  connect(token?: string): void {
    if (token) {
      this.token = token;
    }
    // Idempotent while live: the consumer effect re-fires connect() on every
    // token-state change (a refresh re-renders AuthContext), and the
    // visibility/online listeners also call it. Returning early here means
    // those re-entries don't wipe the reconnect backoff counter or re-arm a
    // deliberately-stopped (session-expired) loop.
    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }
    this.shouldReconnect = true;
    this.reconnectAttempts = 0;
    this.reconnectDelay = 1000;
    this.openSocket();
  }

  private openSocket(): void {
    // Always use the LATEST token: apiClient keeps the refreshed accessToken in
    // localStorage, so reading it here means a reconnect after a token refresh
    // carries a live token rather than the stale one captured at login.
    const token = getAccessToken() || this.token;
    if (!token) {
      console.error('WebSocket: No authentication token available');
      this.setLinkStatus('down');
      return;
    }
    this.token = token;

    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      return;
    }

    this.hadOpen = false;
    // Every path that actually attempts a socket (first connect AND every
    // backoff retry) funnels through here, so this single call covers both.
    this.setLinkStatus('reconnecting');
    try {
      const wsUrl = `${this.getWebSocketUrl()}?token=${encodeURIComponent(token)}`;
      this.ws = new WebSocket(wsUrl);
      this.setupWebSocketHandlers();
    } catch (error) {
      console.error('WebSocket: Failed to create connection', error);
      this.scheduleReconnect();
    }
  }

  private setupWebSocketHandlers(): void {
    if (!this.ws) return;

    this.ws.onopen = () => {
      this.isConnected = true;
      this.hadOpen = true;
      this.didAuthRefresh = false;
      this.reconnectAttempts = 0;
      this.reconnectDelay = 1000;
      this.startHeartbeat();
      this.setLinkStatus('up');

      // Notify handlers about connection
      this.notifyHandlers({
        type: 'connection_status',
        connected: true,
        timestamp: new Date().toISOString()
      });
    };

    this.ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);
        this.notifyHandlers(message);
      } catch (error) {
        console.error('WebSocket: Failed to parse message', error);
      }
    };

    this.ws.onclose = (event) => {
      this.isConnected = false;
      this.stopHeartbeat();
      
      // Notify handlers about disconnection
      this.notifyHandlers({
        type: 'connection_status',
        connected: false,
        code: event.code,
        reason: event.reason,
        timestamp: new Date().toISOString()
      });

      if (!this.shouldReconnect) {
        this.setLinkStatus('down');
        return;
      }
      // 4002 = player profile not found — not retryable.
      if (event.code === 4002) {
        this.setLinkStatus('down');
        return;
      }
      // 4001/'superseded' = a newer tab/device connected as this same user
      // and evicted this socket (WO-RT-EVICTION-SUPERSEDE). This is NOT an
      // auth failure — reconnecting here would just evict the new tab in
      // turn, ping-ponging the eviction forever. Stop the retry loop and
      // surface a "connected elsewhere" state instead.
      if (event.code === 4001 && event.reason === 'superseded') {
        this.shouldReconnect = false;
        this.setLinkStatus('down');
        this.notifyHandlers({
          type: 'connection_superseded',
          message: 'Connected in another tab or device',
          timestamp: new Date().toISOString()
        });
        return;
      }
      // Every remaining branch below retries in some form (refresh-then-retry
      // or plain backoff), so the link is actively being re-established.
      this.setLinkStatus('reconnecting');
      // Auth failure surfaces two ways: the server's explicit 4001 (post-accept
      // close, reason !== 'superseded' — see the eviction branch above) OR,
      // when an expired token is rejected before accept, a handshake that
      // never opened (browser code 1006). In both cases the token is the
      // suspect, so refresh it before retrying instead of looping on a dead
      // token ("WebSocket: Connection error" every interval). A clean drop
      // AFTER a successful open (network blip) just reconnects.
      const authSuspect = event.code === 4001 || !this.hadOpen;
      if (authSuspect && !this.didAuthRefresh) {
        this.reconnectWithRefresh();
      } else {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket: Connection error', error);
      
      // Notify handlers about error
      this.notifyHandlers({
        type: 'connection_error',
        error: 'WebSocket connection error',
        timestamp: new Date().toISOString()
      });
    };
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('WebSocket: Max reconnection attempts reached');
      // No timer is armed past this point (only the online/visibility
      // listeners can restart the loop), so this is a genuine "down", not a
      // still-retrying "reconnecting".
      this.setLinkStatus('down');
      this.notifyHandlers({
        type: 'connection_failed',
        message: 'Failed to reconnect after maximum attempts',
        timestamp: new Date().toISOString()
      });
      return;
    }

    console.warn(`WebSocket: Scheduling reconnect attempt ${this.reconnectAttempts + 1} in ${this.reconnectDelay}ms`);
    
    this.reconnectTimer = window.setTimeout(() => {
      // Re-check at fire time: a logout (disconnect) between scheduling and
      // firing must not resurrect the dead session's socket.
      if (!this.shouldReconnect) return;
      this.reconnectAttempts++;
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000); // Max 30 seconds
      // openSocket() (not connect()) so the backoff counter is preserved and
      // the latest token is used.
      this.openSocket();
    }, this.reconnectDelay);
  }

  // Refresh the access token, then reconnect. Used when a close looks like an
  // auth failure (4001, or a handshake that never opened). If the refresh token
  // is also dead, stop reconnecting and surface a session-expired event rather
  // than error-looping on a token that will never work.
  private reconnectWithRefresh(): void {
    if (this.refreshingAuth) return; // a refresh+reconnect is already in flight
    this.refreshingAuth = true;
    this.didAuthRefresh = true; // one refresh per outage (reset on next open)
    refreshAccessToken()
      .then((newToken) => {
        this.refreshingAuth = false;
        if (!this.shouldReconnect) return;
        if (newToken) {
          // Fresh token now in localStorage; reconnect promptly with it.
          this.reconnectAttempts = 0;
          this.reconnectDelay = 1000;
          this.scheduleReconnect();
        } else {
          this.endSession();
        }
      })
      .catch(() => {
        this.refreshingAuth = false;
        this.endSession();
      });
  }

  // Terminal state: the refresh token is also dead. Stop reconnecting AND null
  // the token so the visibility/online listeners (gated on this.token) cannot
  // restart a loop with a credential that will never work. The REST 401 path
  // owns the redirect to login; this just surfaces the state and ends the
  // error-loop cleanly.
  private endSession(): void {
    this.shouldReconnect = false;
    this.token = null;
    this.setLinkStatus('down');
    this.notifyHandlers({
      type: 'session_expired',
      message: 'Session expired — please sign in again',
      timestamp: new Date().toISOString(),
    });
  }

  private startHeartbeat(): void {
    this.heartbeatInterval = setInterval(() => {
      if (this.isConnected) {
        this.send({
          type: 'heartbeat',
          timestamp: new Date().toISOString()
        });
      }
    }, 30000); // Send heartbeat every 30 seconds
  }

  private stopHeartbeat(): void {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  send(message: WebSocketMessage): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('WebSocket: Cannot send message - not connected');
      return false;
    }

    try {
      this.ws.send(JSON.stringify(message));
      return true;
    } catch (error) {
      console.error('WebSocket: Failed to send message', error);
      return false;
    }
  }

  // Chat methods
  sendChatMessage(content: string, targetType: 'sector' | 'team' | 'global' = 'sector'): boolean {
    return this.send({
      type: 'chat_message',
      content: content.trim(),
      target_type: targetType,
      timestamp: new Date().toISOString()
    });
  }

  // Player info requests
  requestSectorPlayers(): boolean {
    return this.send({
      type: 'request_sector_players',
      timestamp: new Date().toISOString()
    });
  }

  requestTeamPlayers(): boolean {
    return this.send({
      type: 'request_team_players',
      timestamp: new Date().toISOString()
    });
  }

  // ARIA AI Chat methods
  sendARIAMessage(content: string, conversationId?: string, context?: string): boolean {
    // Generate session ID (could be stored in localStorage or state)
    const sessionId = localStorage.getItem('aria_session_id') || 'session_' + Date.now();
    localStorage.setItem('aria_session_id', sessionId);

    const message: ARIAChatMessage = {
      type: 'aria_chat',
      content: content.trim(),
      conversation_id: conversationId,
      context: context || 'general',
      timestamp: new Date().toISOString(),
      session_id: sessionId
    };

    // Add signature for security (simplified client-side signing)
    message.signature = this.generateMessageSignature(message);

    return this.send(message);
  }

  private generateMessageSignature(message: ARIAChatMessage): string {
    // Simple client-side signature - server will validate properly
    const content = JSON.stringify({
      type: message.type,
      timestamp: message.timestamp,
      session_id: message.session_id
    });
    
    // Use a simple hash - real signature would use proper crypto
    return btoa(content).slice(0, 16);
  }

  // Message handler management
  addMessageHandler(handler: MessageHandler): void {
    this.messageHandlers.add(handler);
  }

  removeMessageHandler(handler: MessageHandler): void {
    this.messageHandlers.delete(handler);
  }

  // Records the new status and emits a link_status frame ONLY on an actual
  // change, so repeated calls from idempotent paths (e.g. openSocket()'s
  // already-CONNECTING guard is bypassed above, but disconnect() racing an
  // onclose from the same close() is common) never double-fire.
  private setLinkStatus(status: LinkStatus): void {
    if (this.linkStatus === status) return;
    this.linkStatus = status;
    this.notifyHandlers({
      type: 'link_status',
      status,
      timestamp: new Date().toISOString()
    });
  }

  getLinkStatus(): LinkStatus {
    return this.linkStatus;
  }

  onLinkStatus(callback: (status: LinkStatus) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'link_status') {
        callback((message as LinkStatusMessage).status);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  private notifyHandlers(message: WebSocketMessage): void {
    this.messageHandlers.forEach(handler => {
      try {
        handler(message);
      } catch (error) {
        console.error('WebSocket: Error in message handler', error);
      }
    });
  }

  disconnect(): void {
    this.shouldReconnect = false;
    this.stopHeartbeat();
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    // Drop the token so online/visibility handlers cannot reconnect a
    // logged-out session.
    this.token = null;

    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }

    this.isConnected = false;
    // Set synchronously rather than relying solely on the close event above:
    // disconnect() can be called while a backoff TIMER is pending and no
    // live ws exists (already cleared above), in which case no close event
    // ever fires to flip the status itself.
    this.setLinkStatus('down');
  }

  getConnectionStatus(): {
    connected: boolean;
    reconnectAttempts: number;
    hasToken: boolean;
  } {
    return {
      connected: this.isConnected,
      reconnectAttempts: this.reconnectAttempts,
      hasToken: !!this.token
    };
  }

  // Helper methods for common message types
  onChatMessage(callback: (message: ChatMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'chat_message') {
        callback(message as ChatMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  onPlayerMovement(callback: (message: PlayerMovementMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'player_entered_sector' || message.type === 'player_left_sector') {
        callback(message as PlayerMovementMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  onSectorPlayers(callback: (message: SectorPlayersMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'sector_players') {
        callback(message as SectorPlayersMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  onNotification(callback: (message: NotificationMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'notification') {
        callback(message as NotificationMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  onConnectionStatus(callback: (connected: boolean, details?: any) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'connection_status') {
        callback(message.connected, message);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  // Medal-award realtime push (medal_service.award_medal → send_medal_awarded)
  onMedalAwarded(callback: (message: MedalAwardedMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'medal_awarded') {
        callback(message as MedalAwardedMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  // ARIA AI callback handlers
  onARIAResponse(callback: (message: ARIAResponseMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'aria_response') {
        callback(message as ARIAResponseMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  // ARIA narration push (WO-ARIA-NARRATE-KERNEL) — see ARIANarrationMessage above.
  onARIANarration(callback: (message: ARIANarrationMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'aria_narration') {
        callback(message as ARIANarrationMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }

  // Quantum-shard harvest push (see QuantumHarvestMessage above)
  onQuantumHarvest(callback: (message: QuantumHarvestMessage) => void): () => void {
    const handler = (message: WebSocketMessage) => {
      if (message.type === 'quantum_harvest') {
        callback(message as QuantumHarvestMessage);
      }
    };
    this.addMessageHandler(handler);
    return () => this.removeMessageHandler(handler);
  }
}

// Export singleton instance
export const websocketService = new WebSocketService();
export default websocketService;