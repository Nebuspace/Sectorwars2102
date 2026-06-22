import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from 'react';
import websocketService, {
  WebSocketMessage,
  ChatMessage,
  PlayerMovementMessage,
  SectorPlayersMessage,
  NotificationMessage,
  ARIAResponseMessage
} from '../services/websocket';
import { useAuth } from './AuthContext';

interface WebSocketContextType {
  // Connection status
  isConnected: boolean;
  connectionStatus: string;
  
  // Chat functionality
  chatMessages: ChatMessage[];
  sendChatMessage: (content: string, targetType?: 'sector' | 'team' | 'global') => boolean;
  clearChatMessages: () => void;
  
  // ARIA AI Chat functionality
  sendARIAMessage: (content: string, conversationId?: string, context?: string) => boolean;
  ariaMessages: Array<{
    id: string;
    type: 'user' | 'ai';
    content: string;
    timestamp: string;
    conversationId?: string;
    confidence?: number;
    actions?: Array<{
      type: string;
      [key: string]: any;
    }>;
    suggestions?: string[];
  }>;
  clearARIAMessages: () => void;
  
  // Player presence
  sectorPlayers: Array<{
    user_id: string;
    username: string;
    connected_at: string;
    last_heartbeat: string;
  }>;
  requestSectorPlayers: () => void;
  
  // Notifications
  notifications: NotificationMessage[];
  addNotification: (notification: Omit<NotificationMessage, 'type' | 'timestamp'>) => void;
  removeNotification: (index: number) => void;
  clearNotifications: () => void;
  
  // Player movement tracking
  recentMovements: PlayerMovementMessage[];

  // Player-to-player hails: bumps once per inbound `new_message`
  // notification (message_service._send_notification). Consumers (the
  // COMMS mailbox) watch this counter and refresh the inbox — the badge
  // updates live without a reload. lastNewMessage carries the payload.
  newMessageSignal: number;
  lastNewMessage: {
    message_id: string;
    sender_id: string;
    sender_name: string;
    preview: string;
    sent_at: string | null;
    priority: string;
    // Canon delivery surfaces (messaging.md "Priority levels"): inbox-always,
    // toast for normal+, push for high+, modal for urgent (admin senders).
    delivery: string[];
  } | null;

  // Urgent hails: bumps once per inbound hail whose canon delivery list
  // includes "modal" (priority=urgent from an admin sender). The cockpit's
  // PriorityHailModal watches this to raise an action-interrupting modal;
  // lastUrgentMessage carries the payload to render.
  urgentMessageSignal: number;
  lastUrgentMessage: {
    message_id: string;
    sender_name: string;
    preview: string;
    sent_at: string | null;
  } | null;

  // Medal awards: bumps once per inbound `medal_awarded` push
  // (medal_service.award_medal → enhanced_websocket_service.send_medal_awarded).
  // The MedalShowcase watches this counter to re-fetch its grid live, and a
  // success toast surfaces the decoration. lastMedalAwarded carries the payload.
  medalAwardedSignal: number;
  lastMedalAwarded: {
    medal_id: string;
    medal_name: string | null;
    medal_category: string | null;
    medal_tier: string | null;
    medal_description: string | null;
    medal_icon: string | null;
    awarded_via: string;
  } | null;

  // Colony tick: bumps once per inbound `genesis_progress` (a deployed Genesis
  // device finished forming → 100% / 0h) or `planetary_update` (server-pushed
  // planet-state change) frame. CRT-T1.5-9 §5.1: both frames were SILENTLY
  // DROPPED here (no case in generalHandler) — the colony panel never knew the
  // world ticked. PlanetManager watches this counter to re-fetch /planets/owned
  // live, replacing the locally-guessed formation setInterval poll.
  // lastPlanetaryEvent carries the payload (planet_id, type) for targeted use.
  planetaryEventSignal: number;
  lastPlanetaryEvent: {
    type: 'genesis_progress' | 'planetary_update';
    planet_id: string | null;
    sector_id: number | null;
  } | null;

  // Citadel Research cockpit (CRT-T1.5-9 / CRT-4). Three PUSHED frame types from
  // the now-live governed-flywheel economy, handled in generalHandler below:
  //   • contract_offer    — the HEADLINE: a generated, perishable Research-Directive
  //       offer raised by the sweep on a frontier/contested world (a done world
  //       raises none). High-value toast; the EmpireResearchPanel reads the latest
  //       offer to nudge a refetch of GET /research/offers.
  //   • contract_settled  — one-shot toast ("Overclock on Planet X ended").
  //   • rp_governor_status — fires ONCE on band-cross into the taper, NEVER on a
  //       healthy under-cap player; inbox/toast, NEVER modal. Carries the live
  //       rpPerDay/throughputPct so the panel's headroom readout stays fresh.
  // These bump a single research signal (panel watches it to re-fetch cockpit +
  // offers live); lastContractOffer / lastGovernorStatus carry the payloads.
  researchEventSignal: number;
  lastContractOffer: {
    id: string;
    kind: string;
    planetId: string | null;
    planetName: string | null;
    rpCost: number | null;
    crCost: number | null;
    magnitude: number | null;
    expiresAt: string | null;
  } | null;
  lastGovernorStatus: {
    rpPerDay: number | null;
    throughputPct: number | null;
    ariaText: string | null;
  } | null;

  // Connection management
  connect: () => void;
  disconnect: () => void;
  reconnect: () => void;
}

const WebSocketContext = createContext<WebSocketContextType | undefined>(undefined);

export const useWebSocket = () => {
  const context = useContext(WebSocketContext);
  if (context === undefined) {
    throw new Error('useWebSocket must be used within a WebSocketProvider');
  }
  return context;
};

interface WebSocketProviderProps {
  children: React.ReactNode;
}

export const WebSocketProvider: React.FC<WebSocketProviderProps> = ({ children }) => {
  const { user } = useAuth();
  const token = localStorage.getItem('accessToken');
  const [isConnected, setIsConnected] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState('Disconnected');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [ariaMessages, setAriaMessages] = useState<Array<{
    id: string;
    type: 'user' | 'ai';
    content: string;
    timestamp: string;
    conversationId?: string;
    confidence?: number;
    actions?: Array<{
      type: string;
      [key: string]: any;
    }>;
    suggestions?: string[];
  }>>([]);
  const [sectorPlayers, setSectorPlayers] = useState<Array<{
    user_id: string;
    username: string;
    connected_at: string;
    last_heartbeat: string;
  }>>([]);
  const [notifications, setNotifications] = useState<NotificationMessage[]>([]);
  const [recentMovements, setRecentMovements] = useState<PlayerMovementMessage[]>([]);
  const [newMessageSignal, setNewMessageSignal] = useState(0);
  const [lastNewMessage, setLastNewMessage] = useState<{
    message_id: string;
    sender_id: string;
    sender_name: string;
    preview: string;
    sent_at: string | null;
    priority: string;
    delivery: string[];
  } | null>(null);
  const [urgentMessageSignal, setUrgentMessageSignal] = useState(0);
  const [lastUrgentMessage, setLastUrgentMessage] = useState<{
    message_id: string;
    sender_name: string;
    preview: string;
    sent_at: string | null;
  } | null>(null);
  const [medalAwardedSignal, setMedalAwardedSignal] = useState(0);
  const [lastMedalAwarded, setLastMedalAwarded] = useState<{
    medal_id: string;
    medal_name: string | null;
    medal_category: string | null;
    medal_tier: string | null;
    medal_description: string | null;
    medal_icon: string | null;
    awarded_via: string;
  } | null>(null);
  const [planetaryEventSignal, setPlanetaryEventSignal] = useState(0);
  const [lastPlanetaryEvent, setLastPlanetaryEvent] = useState<{
    type: 'genesis_progress' | 'planetary_update';
    planet_id: string | null;
    sector_id: number | null;
  } | null>(null);
  const [researchEventSignal, setResearchEventSignal] = useState(0);
  const [lastContractOffer, setLastContractOffer] = useState<{
    id: string;
    kind: string;
    planetId: string | null;
    planetName: string | null;
    rpCost: number | null;
    crCost: number | null;
    magnitude: number | null;
    expiresAt: string | null;
  } | null>(null);
  const [lastGovernorStatus, setLastGovernorStatus] = useState<{
    rpPerDay: number | null;
    throughputPct: number | null;
    ariaText: string | null;
  } | null>(null);

  // Keep track of cleanup functions
  const cleanupFunctions = useRef<Array<() => void>>([]);

  // Connection management
  const connect = useCallback(() => {
    if (token) {
      websocketService.connect(token);
    } else {
      console.warn('WebSocket: No token available for connection');
    }
  }, [token]);

  const disconnect = useCallback(() => {
    websocketService.disconnect();
  }, []);

  const reconnect = useCallback(() => {
    disconnect();
    setTimeout(connect, 1000);
  }, [connect, disconnect]);

  // Chat functionality
  const sendChatMessage = useCallback((content: string, targetType: 'sector' | 'team' | 'global' = 'sector') => {
    return websocketService.sendChatMessage(content, targetType);
  }, []);

  const clearChatMessages = useCallback(() => {
    setChatMessages([]);
  }, []);

  // ARIA functionality
  const sendARIAMessage = useCallback((content: string, conversationId?: string, context?: string) => {
    const success = websocketService.sendARIAMessage(content, conversationId, context);
    
    if (success) {
      // Add user message immediately to the local state
      const userMessage = {
        id: `user-${Date.now()}`,
        type: 'user' as const,
        content: content,
        timestamp: new Date().toISOString(),
        conversationId: conversationId
      };
      setAriaMessages(prev => [...prev, userMessage]);
    }
    
    return success;
  }, []);

  const clearARIAMessages = useCallback(() => {
    setAriaMessages([]);
  }, []);

  // Player presence
  const requestSectorPlayers = useCallback(() => {
    websocketService.requestSectorPlayers();
  }, []);

  // Notifications
  const addNotification = useCallback((notification: Omit<NotificationMessage, 'type' | 'timestamp'>) => {
    const newNotification: NotificationMessage = {
      ...notification,
      type: 'notification',
      timestamp: new Date().toISOString()
    };
    
    setNotifications(prev => [newNotification, ...prev].slice(0, 10)); // Keep only last 10
    
    // Auto-remove notification after 5 seconds for non-error messages
    if (notification.level !== 'error') {
      setTimeout(() => {
        setNotifications(prev => prev.filter(n => n.timestamp !== newNotification.timestamp));
      }, 5000);
    }
  }, []);

  const removeNotification = useCallback((index: number) => {
    setNotifications(prev => prev.filter((_, i) => i !== index));
  }, []);

  const clearNotifications = useCallback(() => {
    setNotifications([]);
  }, []);

  // Set up message handlers when component mounts
  useEffect(() => {
    const cleanups: Array<() => void> = [];

    // Connection status handler
    const connectionHandler = websocketService.onConnectionStatus((connected, details) => {
      setIsConnected(connected);
      if (connected) {
        setConnectionStatus('Connected');
      } else {
        setConnectionStatus(details?.reason || 'Disconnected');
        
        // Clear real-time data when disconnected
        setSectorPlayers([]);
      }
    });
    cleanups.push(connectionHandler);

    // Chat message handler
    const chatHandler = websocketService.onChatMessage((message) => {
      setChatMessages(prev => [...prev, message].slice(-50)); // Keep last 50 messages
    });
    cleanups.push(chatHandler);

    // Player movement handler
    const movementHandler = websocketService.onPlayerMovement((message) => {
      setRecentMovements(prev => [message, ...prev].slice(0, 20)); // Keep last 20 movements
      
      // Update sector players list based on movement
      if (message.type === 'player_entered_sector') {
        setSectorPlayers(prev => {
          // Check if player is already in the list
          const exists = prev.some(p => p.user_id === message.user_id);
          if (!exists) {
            return [...prev, {
              user_id: message.user_id,
              username: message.username,
              connected_at: message.timestamp,
              last_heartbeat: message.timestamp
            }];
          }
          return prev;
        });
      } else if (message.type === 'player_left_sector') {
        setSectorPlayers(prev => prev.filter(p => p.user_id !== message.user_id));
      }
    });
    cleanups.push(movementHandler);

    // Sector players handler
    const sectorPlayersHandler = websocketService.onSectorPlayers((message) => {
      setSectorPlayers(message.players);
    });
    cleanups.push(sectorPlayersHandler);

    // Notification handler
    const notificationHandler = websocketService.onNotification((message) => {
      setNotifications(prev => [message, ...prev].slice(0, 10));
      
      // Auto-remove notification after 5 seconds for non-error messages
      if (message.level !== 'error') {
        setTimeout(() => {
          setNotifications(prev => prev.filter(n => n.timestamp !== message.timestamp));
        }, 5000);
      }
    });
    cleanups.push(notificationHandler);

    // ARIA response handler
    const ariaHandler = websocketService.onARIAResponse((message) => {
      const aiMessage = {
        id: `ai-${Date.now()}`,
        type: 'ai' as const,
        content: message.data.message,
        // The plain-WS aria_response frame omits a top-level timestamp; without
        // a fallback this stays undefined and any consumer that sorts the feed
        // by timestamp (AriaTerminalPage) throws on .localeCompare → MFD fault.
        timestamp: message.timestamp ?? new Date().toISOString(),
        conversationId: message.conversation_id,
        confidence: message.data.confidence,
        actions: message.data.actions,
        suggestions: message.data.suggestions
      };
      
      setAriaMessages(prev => [...prev, aiMessage]);
      
      // Show notification for important ARIA responses
      if (message.data.actions && message.data.actions.length > 0) {
        addNotification({
          title: 'ARIA Recommendation',
          content: `ARIA has ${message.data.actions.length} suggestion(s) for you`,
          level: 'info'
        });
      }
    });
    cleanups.push(ariaHandler);

    // Handle other message types
    const generalHandler = (message: WebSocketMessage) => {
      switch (message.type) {
        case 'connection_status':
          // This is handled by the websocketService's onConnectionStatus
          // No additional handling needed here
          break;
          
        case 'heartbeat_ack':
          // Handle heartbeat acknowledgment
          break;
          
        case 'trade_completed':
          addNotification({
            title: 'Trade Completed',
            content: 'A trade was completed in your area',
            level: 'info'
          });
          break;
          
        case 'combat_event':
          addNotification({
            title: 'Combat Activity',
            content: 'Combat activity detected in your sector',
            level: 'warning'
          });
          break;
          
        case 'new_message': {
          // Player-to-player hail (message_service → notification_service).
          // The backend resolves the canon delivery surfaces by priority
          // (messaging.md "Priority levels") and sends them in `delivery`:
          //   • inbox  — ALWAYS present: bump the signal so the COMMS mailbox
          //              refreshes its inbox + unread badge (even `low`, which
          //              is "inbox only" — no toast, but the badge stays live).
          //   • toast  — normal/high/urgent: surface the in-cockpit toast.
          //   • modal  — urgent (admin sender only): raise an interrupt modal.
          // Default to the full normal-priority surface set if a legacy frame
          // arrives without `delivery`, so older servers still toast.
          const sender_name = String(message.sender_name || 'UNKNOWN');
          const preview = String(message.preview || '');
          const sent_at = message.sent_at || null;
          const delivery: string[] = Array.isArray(message.delivery)
            ? message.delivery.map((s: any) => String(s))
            : ['inbox', 'toast'];

          setLastNewMessage({
            message_id: String(message.message_id || ''),
            sender_id: String(message.sender_id || ''),
            sender_name,
            preview,
            sent_at,
            priority: String(message.priority || 'normal'),
            delivery
          });
          // Inbox refresh is unconditional — the persistent record always lands.
          setNewMessageSignal(prev => prev + 1);

          if (delivery.includes('toast')) {
            addNotification({
              title: `Incoming hail from ${message.sender_name || 'unknown contact'}`,
              content: preview || 'New transmission received',
              // urgent reads as a warning-level toast even alongside its modal,
              // so a dismissed modal still leaves a trace in the toast stack.
              level: delivery.includes('modal') ? 'warning' : 'info'
            });
          }

          if (delivery.includes('modal')) {
            setLastUrgentMessage({
              message_id: String(message.message_id || ''),
              sender_name,
              preview,
              sent_at
            });
            setUrgentMessageSignal(prev => prev + 1);
          }
          break;
        }

        case 'medal_awarded':
          // Medal earned through play (medal_service.award_medal). Stash the
          // payload + bump the signal so the MedalShowcase re-fetches its grid,
          // and surface a celebratory toast so the pilot sees the decoration the
          // moment it lands — even when the ranking page isn't open.
          setLastMedalAwarded({
            medal_id: String(message.medal_id || ''),
            medal_name: message.medal_name ?? null,
            medal_category: message.medal_category ?? null,
            medal_tier: message.medal_tier ?? null,
            medal_description: message.medal_description ?? null,
            medal_icon: message.medal_icon ?? null,
            awarded_via: String(message.awarded_via || 'system')
          });
          setMedalAwardedSignal(prev => prev + 1);
          // NOTE: the dedicated gold MedalToast (driven by the signal above) is the
          // medal-award surface — do NOT also push to the generic notifications queue
          // (WO-B6's PriorityHailConsumer now renders that queue, which would double-
          // toast every medal). The MedalToast is the single medal surface.
          break;

        case 'genesis_progress': {
          // A deployed Genesis device finished forming (genesis_service emits
          // 100% / 0h per OWNED planet that just completed — composed pre-commit,
          // broadcast post-commit by the scheduler). CRT-T1.5-9 §5.1: this frame
          // was silently dropped. Bump the colony-refresh signal so PlanetManager
          // re-fetches (the world is now usable) and surface a celebratory toast.
          setLastPlanetaryEvent({
            type: 'genesis_progress',
            planet_id: message.planet_id != null ? String(message.planet_id) : null,
            sector_id: typeof message.sector_id === 'number' ? message.sector_id : null
          });
          setPlanetaryEventSignal(prev => prev + 1);
          addNotification({
            title: 'Colony Formation Complete',
            content: 'A Genesis device finished terraforming — the colony is now usable.',
            level: 'success'
          });
          break;
        }

        case 'planetary_update': {
          // Server-pushed planet-state change to the owner (websocket_service
          // .send_planetary_update). CRT-T1.5-9 §5.1: also silently dropped.
          // Bump the same colony-refresh signal so the open colony panel reflects
          // the new state live. No toast — these are routine state ticks, not a
          // milestone; the silent refresh is the "world ticks on screen" win.
          setLastPlanetaryEvent({
            type: 'planetary_update',
            planet_id: message.planet_id != null ? String(message.planet_id) : null,
            sector_id: typeof message.sector_id === 'number' ? message.sector_id : null
          });
          setPlanetaryEventSignal(prev => prev + 1);
          break;
        }

        case 'contract_offer': {
          // CRT-T1.5-9 §5.2/§5.3: THE HEADLINE ping. The sweep GENERATED (never
          // browsed) a perishable Research-Directive offer on a frontier/contested
          // world — a done/uncontested world raises none (§5.9 #2). Stash the offer
          // + bump the research signal so the EmpireResearchPanel re-fetches its
          // live offer set, and surface a fresh high-value toast carrying ARIA's
          // narration. The transport's priority/delivery escalation ladder governs
          // toast-vs-modal; an offer is routine inbox+toast, never modal.
          const offer = (message.offer && typeof message.offer === 'object') ? message.offer : message;
          const delivery: string[] = Array.isArray(message.delivery)
            ? message.delivery.map((s: any) => String(s))
            : ['inbox', 'toast'];
          setLastContractOffer({
            id: String(offer.id || ''),
            kind: String(offer.kind || ''),
            planetId: offer.planetId != null ? String(offer.planetId) : null,
            planetName: offer.planetName != null ? String(offer.planetName) : null,
            rpCost: typeof offer.rpCost === 'number' ? offer.rpCost : null,
            crCost: typeof offer.crCost === 'number' ? offer.crCost : null,
            magnitude: typeof offer.magnitude === 'number' ? offer.magnitude : null,
            expiresAt: offer.expiresAt != null ? String(offer.expiresAt) : null
          });
          setResearchEventSignal(prev => prev + 1);
          if (delivery.includes('toast')) {
            const where = offer.planetName ? ` on ${offer.planetName}` : '';
            addNotification({
              title: 'Citadel Research — Directive Available',
              content: message.ariaText
                ? String(message.ariaText)
                : `A research directive${where} is available. Open Citadel Research to accept or let it perish.`,
              level: 'info'
            });
          }
          break;
        }

        case 'contract_settled': {
          // CRT-T1.5-9 §5.2: one-shot toast when an active directive expires
          // ("Overclock on Planet X ended"). Bump the research signal so an open
          // panel refreshes its contracts-active count. No modal — purely informational.
          setResearchEventSignal(prev => prev + 1);
          const kind = message.kind ? String(message.kind) : 'Directive';
          const where = message.planetName ? ` on ${message.planetName}` : '';
          addNotification({
            title: 'Citadel Research — Directive Complete',
            content: message.ariaText
              ? String(message.ariaText)
              : `${kind}${where} ended.`,
            level: 'success'
          });
          break;
        }

        case 'rp_governor_status': {
          // CRT-T1.5-9 §5.2/§5.5: fires ONCE on a band-cross into the taper —
          // NEVER on a healthy under-cap player (the server gates emission). Carry
          // the live rpPerDay/throughputPct so the panel's headroom readout reflects
          // the band-cross immediately. Inbox/toast, NEVER modal (§5.2). Copy is
          // day-one-TRUE: it names no non-existent lever (no "Doctrine" — §5.3/§5.10);
          // ARIA's text, when present, is honored; the fallback points at a real
          // T1.5 action (finishing/expanding worlds lifts throughput).
          setLastGovernorStatus({
            rpPerDay: typeof message.rpPerDay === 'number' ? message.rpPerDay : null,
            throughputPct: typeof message.throughputPct === 'number' ? message.throughputPct : null,
            ariaText: message.ariaText != null ? String(message.ariaText) : null
          });
          setResearchEventSignal(prev => prev + 1);
          const delivery: string[] = Array.isArray(message.delivery)
            ? message.delivery.map((s: any) => String(s))
            : ['inbox', 'toast'];
          if (delivery.includes('toast')) {
            const pct = typeof message.throughputPct === 'number' ? ` (throughput ${message.throughputPct}%)` : '';
            addNotification({
              title: 'Citadel Research — Throughput Update',
              content: message.ariaText
                ? String(message.ariaText)
                : `Your empire's research is at full throughput for its current frontier${pct} — finishing or expanding worlds raises it.`,
              level: 'info'
            });
          }
          break;
        }

        case 'admin_broadcast':
          addNotification({
            title: message.title || 'System Message',
            content: message.content || 'Administrative broadcast',
            level: 'info'
          });
          break;
          
        case 'connection_error':
          addNotification({
            title: 'Connection Error',
            content: message.error || 'WebSocket connection error',
            level: 'error'
          });
          break;
          
        case 'connection_failed':
          addNotification({
            title: 'Connection Failed',
            content: message.message || 'Failed to maintain connection',
            level: 'error'
          });
          break;
          
        default:
          // Only log truly unhandled message types, not ones handled by specific handlers
          // (aria_response is consumed by the dedicated ariaHandler above; the
          // generalHandler still sees it, so exclude it from the noise warning.)
          if (!['sector_players', 'connection_status', 'chat_message', 'player_entered_sector', 'player_left_sector', 'notification', 'aria_response', 'medal_awarded', 'genesis_progress', 'planetary_update', 'contract_offer', 'contract_settled', 'rp_governor_status'].includes(message.type)) {
            console.warn('WebSocket: Unhandled message type:', message.type);
          }
      }
    };
    
    websocketService.addMessageHandler(generalHandler);
    cleanups.push(() => websocketService.removeMessageHandler(generalHandler));

    // Store cleanup functions
    cleanupFunctions.current = cleanups;

    return () => {
      cleanups.forEach(cleanup => cleanup());
    };
  }, [addNotification]);

  // Auto-connect when user is authenticated
  useEffect(() => {
    if (user && token) {
      connect();
    } else {
      disconnect();
    }

    // Cleanup on unmount
    return () => {
      disconnect();
    };
  }, [user, token, connect, disconnect]);

  // Auto-request sector players when connected
  useEffect(() => {
    if (isConnected) {
      // Request sector players after a short delay to ensure we're fully connected
      setTimeout(() => {
        requestSectorPlayers();
      }, 1000);
    }
  }, [isConnected, requestSectorPlayers]);

  const contextValue: WebSocketContextType = {
    // Connection status
    isConnected,
    connectionStatus,
    
    // Chat functionality
    chatMessages,
    sendChatMessage,
    clearChatMessages,
    
    // ARIA AI Chat functionality
    sendARIAMessage,
    ariaMessages,
    clearARIAMessages,
    
    // Player presence
    sectorPlayers,
    requestSectorPlayers,
    
    // Notifications
    notifications,
    addNotification,
    removeNotification,
    clearNotifications,

    // Player movement tracking
    recentMovements,

    // Player-to-player hails
    newMessageSignal,
    lastNewMessage,
    urgentMessageSignal,
    lastUrgentMessage,

    // Medal awards
    medalAwardedSignal,
    lastMedalAwarded,

    // Colony ticks (genesis_progress / planetary_update — CRT-T1.5-9 §5.1)
    planetaryEventSignal,
    lastPlanetaryEvent,

    // Citadel Research cockpit (contract_offer / contract_settled / rp_governor_status — CRT-T1.5-9 §5.2)
    researchEventSignal,
    lastContractOffer,
    lastGovernorStatus,

    // Connection management
    connect,
    disconnect,
    reconnect
  };

  return (
    <WebSocketContext.Provider value={contextValue}>
      {children}
    </WebSocketContext.Provider>
  );
};

export default WebSocketProvider;