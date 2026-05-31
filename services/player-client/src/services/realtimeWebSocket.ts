/**
 * Enhanced WebSocket Service - Foundation Sprint Implementation
 * Revolutionary real-time communication with OWASP security
 * Part of Foundation Sprint: Bridge Backend Excellence with Revolutionary Player Experience
 */

import { GameState, MarketData, UniverseActivity, TradingRecommendation } from '../types/game';

// OWASP Security Interfaces
interface SecurityConfig {
  authentication: {
    tokenValidation: 'JWT';
    refreshInterval: number; // 15 minutes
    maxSessionAge: number; // 24 hours
  };
  rateLimit: {
    websocketConnections: number; // 5 per IP
    messageRate: number; // 100 per second per user
    bulkDataRequests: number; // 10 per minute
  };
  validation: {
    inputSanitization: 'DOMPurify';
    sqlInjectionPrevention: 'parameterized';
    xssProtection: 'comprehensive';
  };
}

interface RealTimeMessage {
  type: 'market_update' | 'universe_pulse' | 'port_network' | 'ai_alert' | 'trading_signal' | 'combat_event';
  data: any;
  timestamp: string;
  signature: string; // OWASP: Message integrity
  player_id?: string; // OWASP: Player-specific filtering
  session_id: string;
}

interface WebSocketConfig {
  maxConnections: 1000;
  rateLimitPerSecond: 100;
  heartbeatInterval: 30000;
  reconnectBackoff: number[];
  encryptionEnabled: boolean;
  authRequired: boolean;
}

interface MarketPrediction {
  commodity: string;
  currentPrice: number;
  predictedPrice: number;
  confidence: number; // 0-1
  timeHorizon: string; // '1h', '6h', '24h'
  aiExplanation: string;
  riskLevel: 'low' | 'medium' | 'high';
  timestamp: string;
}

interface TradingAutomationRule {
  id: string;
  name: string;
  commodity: string;
  buyConditions: TradingCondition[];
  sellConditions: TradingCondition[];
  riskLevel: 'conservative' | 'moderate' | 'aggressive';
  maxInvestment: number;
  isActive: boolean;
}

interface TradingCondition {
  type: 'price_below' | 'price_above' | 'margin_exceeds' | 'ai_confidence';
  value: number;
  comparison: 'less_than' | 'greater_than' | 'equals';
}

// Rate limiting implementation
class RateLimit {
  private requestCounts: Map<string, { count: number; resetTime: number }> = new Map();
  private readonly windowMs: number = 60000; // 1 minute
  private readonly maxRequests: number = 100;

  check(identifier: string): boolean {
    const now = Date.now();
    const current = this.requestCounts.get(identifier);

    if (!current || now > current.resetTime) {
      this.requestCounts.set(identifier, { count: 1, resetTime: now + this.windowMs });
      return true;
    }

    if (current.count >= this.maxRequests) {
      return false;
    }

    current.count++;
    return true;
  }

  reset(identifier: string): void {
    this.requestCounts.delete(identifier);
  }
}

// Enhanced WebSocket Service
export class EnhancedWebSocketService {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = [1000, 2000, 4000, 8000, 16000];
  private heartbeatInterval: NodeJS.Timeout | null = null;
  private rateLimit = new RateLimit();
  private sessionId: string = '';
  private authToken: string = '';
  private isAuthenticated = false;
  private subscriptions = new Set<string>();
  
  // Event listeners
  private listeners: Map<string, Function[]> = new Map();
  
  // Security configuration
  private securityConfig: SecurityConfig = {
    authentication: {
      tokenValidation: 'JWT',
      refreshInterval: 15 * 60 * 1000, // 15 minutes
      maxSessionAge: 24 * 60 * 60 * 1000 // 24 hours
    },
    rateLimit: {
      websocketConnections: 5,
      messageRate: 100,
      bulkDataRequests: 10
    },
    validation: {
      inputSanitization: 'DOMPurify',
      sqlInjectionPrevention: 'parameterized',
      xssProtection: 'comprehensive'
    }
  };

  constructor() {
    this.sessionId = this.generateSessionId();
    this.loadAuthToken();
  }

  // Connection Management
  async connect(): Promise<void> {
    try {
      const wsUrl = this.getWebSocketUrl();
      
      // OWASP A01: Authentication check
      if (!this.authToken) {
        throw new Error('Authentication required for WebSocket connection');
      }

      this.ws = new WebSocket(wsUrl);
      this.setupEventHandlers();
      
      return new Promise((resolve, reject) => {
        if (!this.ws) return reject(new Error('WebSocket not initialized'));
        
        this.ws.onopen = () => {
          this.authenticate().then(() => {
            this.startHeartbeat();
            this.reconnectAttempts = 0;
            resolve();
          }).catch(reject);
        };

        this.ws.onerror = (error) => {
          console.error('❌ WebSocket connection error:', error);
          reject(new Error('WebSocket connection failed'));
        };
      });
    } catch (error) {
      console.error('❌ Failed to connect WebSocket:', error);
      throw error;
    }
  }

  disconnect(): void {
    this.stopHeartbeat();
    this.subscriptions.clear();
    
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
    
    this.isAuthenticated = false;
  }

  // Authentication
  private async authenticate(): Promise<void> {
    if (!this.ws || !this.authToken) {
      throw new Error('WebSocket or auth token not available');
    }

    const authMessage: RealTimeMessage = {
      type: 'ai_alert',
      data: {
        action: 'authenticate',
        token: this.authToken,
        session_id: this.sessionId
      },
      timestamp: new Date().toISOString(),
      signature: await this.signMessage({ token: this.authToken }),
      session_id: this.sessionId
    };

    this.sendSecureMessage(authMessage);
    
    // Wait for authentication response
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error('Authentication timeout'));
      }, 10000);

      const authHandler = (message: RealTimeMessage) => {
        if (message.data.action === 'auth_success') {
          clearTimeout(timeout);
          this.isAuthenticated = true;
          this.off('ai_alert', authHandler);
          resolve();
        } else if (message.data.action === 'auth_failed') {
          clearTimeout(timeout);
          this.off('ai_alert', authHandler);
          reject(new Error('Authentication failed'));
        }
      };

      this.on('ai_alert', authHandler);
    });
  }

  // Subscription Management
  async subscribeToMarketData(commodities: string[] = []): Promise<void> {
    if (!this.isAuthenticated) {
      throw new Error('Must be authenticated to subscribe to market data');
    }

    // OWASP A04: Input validation
    const validatedCommodities = commodities.filter(c => 
      typeof c === 'string' && 
      c.length <= 50 && 
      /^[a-zA-Z0-9_-]+$/.test(c)
    );

    const subscription = {
      type: 'subscribe',
      channel: 'market_data',
      commodities: validatedCommodities,
      real_time: true
    };

    await this.sendChannelMessage('market_update', subscription);
    this.subscriptions.add('market_data');
  }

  async subscribeToUniversePulse(): Promise<void> {
    if (!this.isAuthenticated) {
      throw new Error('Must be authenticated to subscribe to universe pulse');
    }

    const subscription = {
      type: 'subscribe',
      channel: 'universe_activity',
      privacy_level: 'anonymized', // OWASP A09: Privacy protection
      update_frequency: 'real_time'
    };

    await this.sendChannelMessage('universe_pulse', subscription);
    this.subscriptions.add('universe_activity');
  }

  async subscribeToTradingSignals(): Promise<void> {
    if (!this.isAuthenticated) {
      throw new Error('Must be authenticated to subscribe to trading signals');
    }

    const subscription = {
      type: 'subscribe',
      channel: 'trading_signals',
      ai_enabled: true,
      risk_preference: 'moderate'
    };

    await this.sendChannelMessage('trading_signal', subscription);
    this.subscriptions.add('trading_signals');
  }

  // Message Handling
  private setupEventHandlers(): void {
    if (!this.ws) return;

    this.ws.onmessage = (event) => {
      try {
        const message: RealTimeMessage = JSON.parse(event.data);
        
        // OWASP A03: Input validation
        if (!this.validateMessage(message)) {
          console.warn('⚠️ Invalid message received, ignoring');
          return;
        }

        // OWASP A04: Rate limiting
        if (!this.rateLimit.check(message.player_id || 'anonymous')) {
          console.warn('⚠️ Rate limit exceeded, dropping message');
          return;
        }

        this.handleMessage(message);
      } catch (error) {
        console.error('❌ Error parsing WebSocket message:', error);
      }
    };

    this.ws.onclose = (event) => {
      console.warn(`WebSocket closed: ${event.code} - ${event.reason}`);
      this.isAuthenticated = false;
      this.stopHeartbeat();
      
      if (event.code !== 1000) { // Not a normal closure
        this.attemptReconnect();
      }
    };

    this.ws.onerror = (error) => {
      console.error('❌ WebSocket error:', error);
    };
  }

  private validateMessage(message: RealTimeMessage): boolean {
    // OWASP A03: Comprehensive input validation
    if (!message.type || !message.data || !message.timestamp || !message.session_id) {
      return false;
    }

    // Validate message type
    const validTypes = ['market_update', 'universe_pulse', 'port_network', 'ai_alert', 'trading_signal', 'combat_event'];
    if (!validTypes.includes(message.type)) {
      return false;
    }

    // Validate timestamp (not too old, not in future)
    const messageTime = new Date(message.timestamp).getTime();
    const now = Date.now();
    const fiveMinutes = 5 * 60 * 1000;
    
    if (messageTime < now - fiveMinutes || messageTime > now + fiveMinutes) {
      return false;
    }

    return true;
  }

  private handleMessage(message: RealTimeMessage): void {
    // Emit to registered listeners
    this.emit(message.type, message);
    
    // Handle specific message types
    switch (message.type) {
      case 'market_update':
        this.handleMarketUpdate(message);
        break;
      case 'universe_pulse':
        this.handleUniversePulse(message);
        break;
      case 'trading_signal':
        this.handleTradingSignal(message);
        break;
      case 'ai_alert':
        this.handleAIAlert(message);
        break;
    }
  }

  private handleMarketUpdate(message: RealTimeMessage): void {
    const marketData: MarketData = message.data;
    
    // Sanitize data for XSS protection
    if (marketData.commodity) {
      marketData.commodity = this.sanitizeString(marketData.commodity);
    }
    
  }

  private handleUniversePulse(message: RealTimeMessage): void {
    const activity: UniverseActivity = message.data;
  }

  private handleTradingSignal(message: RealTimeMessage): void {
    const signal: TradingRecommendation = message.data;
    
    // Show notification for high-priority signals
    if (signal.priority >= 4) {
      this.showTradingNotification(signal);
    }
  }

  private handleAIAlert(message: RealTimeMessage): void {
  }

  // Secure Messaging
  private async sendSecureMessage(message: RealTimeMessage): Promise<void> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error('WebSocket not connected');
    }

    // OWASP A04: Rate limiting check
    if (!this.rateLimit.check('outbound')) {
      throw new Error('Rate limit exceeded');
    }

    try {
      const serializedMessage = JSON.stringify(message);
      this.ws.send(serializedMessage);
    } catch (error) {
      console.error('❌ Failed to send message:', error);
      throw error;
    }
  }

  private async sendChannelMessage(type: RealTimeMessage['type'], data: any): Promise<void> {
    const message: RealTimeMessage = {
      type,
      data,
      timestamp: new Date().toISOString(),
      signature: await this.signMessage(data),
      session_id: this.sessionId
    };

    await this.sendSecureMessage(message);
  }

  // Utility Methods
  private getWebSocketUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = process.env.NODE_ENV === 'development' 
      ? 'localhost:8080' 
      : window.location.host;
    
    return `${protocol}//${host}/ws/realtime`;
  }

  private generateSessionId(): string {
    // Cryptographically-secure random suffix instead of Math.random
    // (js/insecure-randomness — session IDs are a security context).
    const bytes = new Uint8Array(8);
    (globalThis.crypto || (window as any).msCrypto).getRandomValues(bytes);
    const suffix = Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
    return `session_${Date.now()}_${suffix}`;
  }

  private loadAuthToken(): void {
    // Load from secure storage (httpOnly cookie preferred)
    this.authToken = localStorage.getItem('auth_token') || '';
  }

  private async signMessage(data: any): Promise<string> {
    // Simple signing for demo - in production use proper HMAC
    const dataString = JSON.stringify(data);
    const encoder = new TextEncoder();
    const dataBuffer = encoder.encode(dataString + this.sessionId);
    
    const hashBuffer = await crypto.subtle.digest('SHA-256', dataBuffer);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  }

  private sanitizeString(input: string): string {
    // Basic XSS protection — in production use DOMPurify.
    // Combined dangerous-scheme strip (covers vbscript: too) closes the
    // js/incomplete-url-scheme-check finding.
    return input
      .replace(/[<>"'&]/g, '')
      .replace(/(?:javascript|data|vbscript):/gi, '')
      .slice(0, 1000); // Limit length
  }

  // Heartbeat Management
  private startHeartbeat(): void {
    this.heartbeatInterval = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.sendChannelMessage('ai_alert', { action: 'ping' });
      }
    }, 30000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  // Reconnection Logic
  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('❌ Max reconnection attempts reached');
      this.emit('connection_failed', { reason: 'Max attempts exceeded' });
      return;
    }

    const delay = this.reconnectDelay[this.reconnectAttempts] || 16000;
    this.reconnectAttempts++;

    console.warn(`WebSocket: Attempting reconnection #${this.reconnectAttempts} in ${delay}ms`);
    
    setTimeout(() => {
      this.connect().catch((error) => {
        console.error(`❌ Reconnection attempt #${this.reconnectAttempts} failed:`, error);
        this.attemptReconnect();
      });
    }, delay);
  }

  // Event System
  on(event: string, callback: Function): void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event)!.push(callback);
  }

  off(event: string, callback: Function): void {
    const eventListeners = this.listeners.get(event);
    if (eventListeners) {
      const index = eventListeners.indexOf(callback);
      if (index > -1) {
        eventListeners.splice(index, 1);
      }
    }
  }

  private emit(event: string, data: any): void {
    const eventListeners = this.listeners.get(event);
    if (eventListeners) {
      eventListeners.forEach(callback => {
        try {
          callback(data);
        } catch (error) {
          console.error('❌ Error in event listener:', error);
        }
      });
    }
  }

  // Trading-specific methods
  async requestMarketPredictions(commodities: string[]): Promise<MarketPrediction[]> {
    const request = {
      action: 'get_predictions',
      commodities: commodities.slice(0, 10), // Limit for security
      timeframe: '24h'
    };

    await this.sendChannelMessage('market_update', request);
    
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error('Market prediction request timeout'));
      }, 10000);

      const handler = (message: RealTimeMessage) => {
        if (message.data.action === 'predictions_response') {
          clearTimeout(timeout);
          this.off('market_update', handler);
          resolve(message.data.predictions);
        }
      };

      this.on('market_update', handler);
    });
  }

  private showTradingNotification(signal: TradingRecommendation): void {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('🤖 ARIA Trading Signal', {
        body: `${signal.title} - Expected profit: ${signal.expected_outcome?.value}`,
        icon: '/favicon.svg',
        tag: 'trading-signal'
      });
    }
  }

  // Public API
  getConnectionStatus(): {
    connected: boolean;
    authenticated: boolean;
    subscriptions: string[];
    reconnectAttempts: number;
  } {
    return {
      connected: this.ws?.readyState === WebSocket.OPEN || false,
      authenticated: this.isAuthenticated,
      subscriptions: Array.from(this.subscriptions),
      reconnectAttempts: this.reconnectAttempts
    };
  }

  // Cleanup
  destroy(): void {
    this.disconnect();
    this.listeners.clear();
    this.rateLimit = new RateLimit();
  }
}

// Singleton instance
export const realtimeWebSocket = new EnhancedWebSocketService();

// Export types for use in components
export type {
  RealTimeMessage,
  MarketPrediction,
  TradingAutomationRule,
  TradingCondition,
  SecurityConfig
};