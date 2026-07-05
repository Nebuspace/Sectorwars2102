// Raw WebSocket implementation for admin UI
export interface WebSocketMessage {
  type: string;
  [key: string]: any;
}

export interface WebSocketEvents {
  // Economy events
  'economy:market-update': (data: any) => void;
  'economy:price-change': (data: any) => void;
  'economy:intervention': (data: any) => void;
  
  // Combat events
  'combat:new-event': (data: any) => void;
  'combat:dispute-filed': (data: any) => void;
  'combat:stats-update': (data: any) => void;
  
  // Fleet events
  'fleet:status-change': (data: any) => void;
  'fleet:maintenance-alert': (data: any) => void;
  'fleet:emergency': (data: any) => void;
  
  // Team events
  'team:update': (data: any) => void;
  'team:alliance-change': (data: any) => void;
  'team:member-change': (data: any) => void;
  
  // Player events
  'player:status-change': (data: any) => void;
  'player:alert': (data: any) => void;
  'player:achievement': (data: any) => void;

  // Moderation events
  'flagged:message:alert': (data: any) => void;

  // Universe events
  'universe:sector-update': (data: any) => void;
  'universe:port-update': (data: any) => void;
  'universe:planet-update': (data: any) => void;
  
  // System events
  'system:maintenance': (data: any) => void;
  'system:announcement': (data: any) => void;
  'system:alert': (data: any) => void;
  'system:performance': (data: any) => void;
  'system:security-event': (data: any) => void;

  // AI / ARIA events
  'ai:model-update': (data: any) => void;
  'ai:prediction-made': (data: any) => void;
  'ai:recommendation-sent': (data: any) => void;
  'ai:profile-updated': (data: any) => void;
  'ai:training-complete': (data: any) => void;
  'ai:accuracy-update': (data: any) => void;
  'ai:route-update': (data: any) => void;
  'ai:route-stats-update': (data: any) => void;
  'ai:segment-update': (data: any) => void;
  'ai:trend-update': (data: any) => void;

  // Connection events
  'connection:established': (data: any) => void;
  'connection:lost': (data: any) => void;
  'connection:error': (data: any) => void;
}

type EventHandler<T = any> = (data: T) => void;

class AdminWebSocketService {
  private ws: WebSocket | null = null;
  private token: string | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private heartbeatInterval: NodeJS.Timeout | null = null;
  private eventHandlers: Map<string, Set<EventHandler>> = new Map();
  private connected = false;
  private shouldReconnect = true;
  private reconnectTimeoutId: NodeJS.Timeout | null = null;
  private _gaveUp = false;
  private onGaveUpCallbacks: Set<() => void> = new Set();

  constructor() {
    this.setupEventListeners();
  }

  private setupEventListeners() {
    // Handle page visibility changes
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && !this.connected && this.token) {
        this.connect(this.token);
      }
    });

    // Handle online/offline events
    window.addEventListener('online', () => {
      if (!this.connected && this.token) {
        this.connect(this.token);
      }
    });

    window.addEventListener('offline', () => {
      this.disconnect();
    });
  }

  private getWebSocketUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;

    // Check for GitHub Codespaces environment - connect directly to gameserver
    if (host.includes('.app.github.dev')) {
      console.log('Admin WebSocket: GitHub Codespaces detected - using direct gameserver WebSocket');
      const gameserverHost = host.replace('-3001.app.github.dev', '-8080.app.github.dev');
      return `${protocol}//${gameserverHost}/api/v1/ws/admin`;
    }

    // For all other environments (localhost, Tailscale IP, etc.),
    // use the current host and let the Vite dev proxy forward to gameserver
    return `${protocol}//${host}/api/v1/ws/admin`;
  }

  async connect(token: string): Promise<void> {
    this.token = token;
    this.shouldReconnect = true;
    this._gaveUp = false;

    if (!this.token) {
      console.error('Admin WebSocket: No authentication token provided');
      throw new Error('No authentication token provided');
    }

    if (this.ws && (this.ws.readyState === WebSocket.CONNECTING || this.ws.readyState === WebSocket.OPEN)) {
      console.log('Admin WebSocket: Already connected or connecting');
      return;
    }

    return new Promise((resolve, reject) => {
      try {
        const wsUrl = `${this.getWebSocketUrl()}?token=${encodeURIComponent(token)}`;
        console.log('Admin WebSocket: Connecting to', wsUrl.replace(token, '[TOKEN]'));
        
        this.ws = new WebSocket(wsUrl);
        
        this.ws.onopen = () => {
          console.log('Admin WebSocket: Connected successfully');
          this.connected = true;
          this.reconnectAttempts = 0;
          this.reconnectDelay = 1000;
          this.startHeartbeat();
          
          // Emit connection established event
          this.emit('connection:established', {
            timestamp: new Date().toISOString()
          });
          
          resolve();
        };

        this.ws.onerror = (error) => {
          console.error('Admin WebSocket: Connection error', error);
          this.emit('connection:error', { error });
          
          if (this.reconnectAttempts === 0) {
            reject(error);
          }
        };

        this.ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data) as WebSocketMessage;
            this.handleMessage(message);
          } catch (error) {
            console.error('Admin WebSocket: Failed to parse message', error);
          }
        };

        this.ws.onclose = (event) => {
          console.log('Admin WebSocket: Connection closed', event.code, event.reason);
          this.connected = false;
          this.stopHeartbeat();
          
          this.emit('connection:lost', {
            code: event.code,
            reason: event.reason,
            timestamp: new Date().toISOString()
          });

          if (this.shouldReconnect && event.code !== 1000) {
            this.scheduleReconnect();
          }
        };
      } catch (error) {
        console.error('Admin WebSocket: Failed to create connection', error);
        reject(error);
      }
    });
  }

  disconnect(): void {
    console.log('Admin WebSocket: Disconnecting');
    this.shouldReconnect = false;
    this.stopHeartbeat();
    this.clearReconnectTimeout();
    
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
    
    this.connected = false;
    this.token = null;
  }

  private handleMessage(message: WebSocketMessage): void {
    // Convert message type to event format if needed
    // e.g., "combat_new_event" -> "combat:new-event"
    // (the trailing count arg on String.replace is invalid and was ignored at
    // runtime — removed; the chain still normalises separators to ':')
    const eventType = message.type.replace(/_/g, ':').replace(/:/g, '-').replace(/-/g, ':');
    
    // Emit to specific event handlers
    this.emit(eventType, message);
    
    // Also emit to generic message handlers
    this.emit('message', message);
  }

  private emit(event: string, data: any): void {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.forEach(handler => {
        try {
          handler(data);
        } catch (error) {
          // Pass `event` as a separate console arg rather than interpolating
          // into the format string — console.error treats %s/%d as format
          // specifiers, so an event name containing them would otherwise be
          // mis-interpreted (js/tainted-format-string).
          console.error('Admin WebSocket: Error in event handler for', event, error);
        }
      });
    }
  }

  on<K extends keyof WebSocketEvents>(event: K, handler: WebSocketEvents[K]): () => void {
    if (!this.eventHandlers.has(event)) {
      this.eventHandlers.set(event, new Set());
    }
    
    const handlers = this.eventHandlers.get(event)!;
    handlers.add(handler as EventHandler);
    
    // Return unsubscribe function
    return () => {
      handlers.delete(handler as EventHandler);
      if (handlers.size === 0) {
        this.eventHandlers.delete(event);
      }
    };
  }

  off<K extends keyof WebSocketEvents>(event: K, handler?: WebSocketEvents[K]): void {
    if (handler) {
      const handlers = this.eventHandlers.get(event);
      if (handlers) {
        handlers.delete(handler as EventHandler);
        if (handlers.size === 0) {
          this.eventHandlers.delete(event);
        }
      }
    } else {
      // Remove all handlers for this event
      this.eventHandlers.delete(event);
    }
  }

  send(event: string, data: any): void {
    if (!this.connected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('Admin WebSocket: Cannot send message - not connected');
      return;
    }

    const message = {
      type: event,
      ...data,
      timestamp: new Date().toISOString()
    };

    try {
      this.ws.send(JSON.stringify(message));
    } catch (error) {
      console.error('Admin WebSocket: Failed to send message', error);
    }
  }

  isConnected(): boolean {
    return this.connected && this.ws?.readyState === WebSocket.OPEN;
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    
    // Send heartbeat every 30 seconds
    this.heartbeatInterval = setInterval(() => {
      if (this.connected && this.ws?.readyState === WebSocket.OPEN) {
        this.send('heartbeat', { timestamp: Date.now() });
      } else {
        this.stopHeartbeat();
      }
    }, 30000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  private scheduleReconnect(): void {
    if (!this.shouldReconnect || this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log('Admin WebSocket: Max reconnection attempts reached, giving up');
      this._gaveUp = true;
      this.onGaveUpCallbacks.forEach(cb => { try { cb(); } catch {} });
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), 30000);
    
    console.log(`Admin WebSocket: Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
    
    this.reconnectTimeoutId = setTimeout(() => {
      if (this.token && this.shouldReconnect) {
        this.connect(this.token).catch(error => {
          console.error('Admin WebSocket: Reconnection failed', error);
        });
      }
    }, delay);
  }

  private clearReconnectTimeout(): void {
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
      this.reconnectTimeoutId = null;
    }
  }

  /** Returns true when max reconnect attempts were exhausted */
  hasGivenUp(): boolean {
    return this._gaveUp;
  }

  /** Register a callback for when reconnection is abandoned */
  onGaveUp(cb: () => void): () => void {
    this.onGaveUpCallbacks.add(cb);
    return () => { this.onGaveUpCallbacks.delete(cb); };
  }

  // Compatibility method for smooth transition
  getSocket(): null {
    // Return null as we don't expose the raw WebSocket
    return null;
  }
}

// Create singleton instance
export const websocketService = new AdminWebSocketService();

// For backward compatibility
export const webSocketService = websocketService;
export default websocketService;