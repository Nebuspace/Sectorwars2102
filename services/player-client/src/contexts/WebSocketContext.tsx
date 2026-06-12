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
        timestamp: message.timestamp,
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
          
        case 'new_message':
          // Player-to-player hail (message_service._send_notification).
          // Stash the payload + bump the signal so the COMMS mailbox can
          // refresh its inbox, and surface a toast for pilots who aren't
          // watching the COMMS monitor.
          setLastNewMessage({
            message_id: String(message.message_id || ''),
            sender_id: String(message.sender_id || ''),
            sender_name: String(message.sender_name || 'UNKNOWN'),
            preview: String(message.preview || ''),
            sent_at: message.sent_at || null,
            priority: String(message.priority || 'normal')
          });
          setNewMessageSignal(prev => prev + 1);
          addNotification({
            title: `Incoming hail from ${message.sender_name || 'unknown contact'}`,
            content: message.preview || 'New transmission received',
            level: 'info'
          });
          break;

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
          if (!['sector_players', 'connection_status', 'chat_message', 'player_entered_sector', 'player_left_sector', 'notification'].includes(message.type)) {
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