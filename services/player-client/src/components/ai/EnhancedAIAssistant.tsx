/**
 * Enhanced AI Assistant - Revolutionary ARIA Interface
 * Comprehensive cross-system AI intelligence with security-first design
 * 
 * Security Features:
 * - XSS prevention through DOMPurify sanitization
 * - Input validation and length limits
 * - Secure WebSocket communication
 * - Rate limiting on client side
 * - CSRF protection via tokens
 */

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { 
  Send, 
  Mic, 
  MicOff, 
  Brain, 
  TrendingUp, 
  Shield, 
  Zap, 
  Settings,
  Minimize2,
  Maximize2,
  X,
  Star,
  AlertTriangle,
  CheckCircle,
  Clock,
  Target,
  Loader
} from 'lucide-react';
import DOMPurify from 'dompurify';
import { useAuth } from '../../contexts/AuthContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import './enhanced-ai-assistant.css';

// Types for enhanced AI interactions
interface AIRecommendation {
  id: string;
  category: 'trading' | 'combat' | 'colony' | 'port' | 'strategic';
  recommendation_type: string;
  title: string;
  summary: string;
  priority: number;
  risk_assessment: 'very_low' | 'low' | 'medium' | 'high' | 'very_high';
  confidence: number;
  expected_outcome: {
    type: string;
    value: number;
    currency?: string;
    probability?: number;
  };
  expires_at: string;
  security_clearance_required: string;
}

interface ConversationMessage {
  id: string;
  type: 'user' | 'ai';
  content: string;
  timestamp: string;
  intent?: {
    primary_intent: string;
    confidence: number;
  };
  recommendations?: AIRecommendation[];
  metadata?: {
    response_time?: number;
    confidence?: number;
  };
}

interface AssistantStatus {
  assistant_id: string;
  assistant_name: string;
  security_level: string;
  api_usage: {
    quota: number;
    used: number;
    remaining: number;
  };
  total_interactions: number;
  last_active: string;
  access_permissions: {
    trading: boolean;
    combat: boolean;
    colony: boolean;
    port: boolean;
  };
}

interface EnhancedAIAssistantProps {
  isMinimized?: boolean;
  onToggleMinimize?: () => void;
  theme?: 'dark' | 'light';
}

const EnhancedAIAssistant: React.FC<EnhancedAIAssistantProps> = ({
  isMinimized = false,
  onToggleMinimize,
  theme = 'dark'
}) => {
  // State management
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [recommendations, setRecommendations] = useState<AIRecommendation[]>([]);
  const [assistantStatus, setAssistantStatus] = useState<AssistantStatus | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [selectedSystems, setSelectedSystems] = useState(['trading']);
  const [showSettings, setShowSettings] = useState(false);
  
  // Security and performance state
  const [rateLimitWarning, setRateLimitWarning] = useState(false);
  const [lastRequestTime, setLastRequestTime] = useState(0);
  const [requestCount, setRequestCount] = useState(0);
  
  // Refs
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<any>(null);
  
  // Context hooks
  const { user } = useAuth();
  const { sendARIAMessage, ariaMessages, clearARIAMessages, isConnected } = useWebSocket();

  // Security constants
  const MAX_MESSAGE_LENGTH = 4000;
  const MIN_REQUEST_INTERVAL = 1000; // 1 second between requests
  const MAX_REQUESTS_PER_MINUTE = 30;
  
  // Memoized API base URL
  const API_BASE_URL = useMemo(() => {
    if (typeof window !== 'undefined') {
      const protocol = window.location.protocol;
      const hostname = window.location.hostname;

      // Detect GitHub Codespaces
      if (hostname.includes('app.github.dev')) {
        return `${protocol}//${hostname.replace('-3000', '-8080')}`;
      }

      // Detect Replit
      if (hostname.includes('repl.co')) {
        return `${protocol}//${hostname}:8080`;
      }

      // Same-origin default: the Vite proxy / nginx gateway route /api to
      // the gameserver in every tier. Hardcoding localhost:8080 made ARIA
      // unreachable from any browser not on the dev box.
      return window.location.origin;
    }
    return '';
  }, []);

  // Auto-scroll to bottom of messages
  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [ariaMessages, scrollToBottom]);

  // Handle loading state when new messages arrive
  useEffect(() => {
    if (ariaMessages.length > 0) {
      const lastMessage = ariaMessages[ariaMessages.length - 1];
      if (lastMessage.type === 'ai') {
        setIsLoading(false);
      }
    }
  }, [ariaMessages]);

  // Initialize assistant status
  useEffect(() => {
    if (user && isOpen) {
      fetchAssistantStatus();
      fetchRecommendations();
    }
  }, [user, isOpen]);

  // Speech recognition setup
  useEffect(() => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition = (window as any).webkitSpeechRecognition || (window as any).SpeechRecognition;
      recognitionRef.current = new SpeechRecognition();
      recognitionRef.current.continuous = false;
      recognitionRef.current.interimResults = false;
      recognitionRef.current.lang = 'en-US';
      
      recognitionRef.current.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        setInputValue(DOMPurify.sanitize(transcript.trim()));
        setIsListening(false);
      };
      
      recognitionRef.current.onerror = () => {
        setIsListening(false);
      };
      
      recognitionRef.current.onend = () => {
        setIsListening(false);
      };
    }
  }, []);

  // Rate limiting check
  const checkRateLimit = useCallback(() => {
    const now = Date.now();
    const timeSinceLastRequest = now - lastRequestTime;
    
    if (timeSinceLastRequest < MIN_REQUEST_INTERVAL) {
      setRateLimitWarning(true);
      setTimeout(() => setRateLimitWarning(false), 3000);
      return false;
    }
    
    // Reset request count every minute
    const oneMinuteAgo = now - 60000;
    if (lastRequestTime < oneMinuteAgo) {
      setRequestCount(1);
    } else {
      setRequestCount(prev => prev + 1);
    }
    
    if (requestCount >= MAX_REQUESTS_PER_MINUTE) {
      setRateLimitWarning(true);
      setTimeout(() => setRateLimitWarning(false), 5000);
      return false;
    }
    
    setLastRequestTime(now);
    return true;
  }, [lastRequestTime, requestCount]);

  // Note: HTTP API calls removed - now using WebSocket

  // Fetch assistant status (simplified for WebSocket)
  const fetchAssistantStatus = useCallback(async () => {
    // Mock status for now - in full implementation would come via WebSocket
    setAssistantStatus({
      assistant_id: 'aria-ws-1',
      assistant_name: 'ARIA (WebSocket)',
      security_level: 'HIGH',
      api_usage: { quota: 1000, used: 0, remaining: 1000 },
      total_interactions: 0,
      last_active: new Date().toISOString(),
      access_permissions: {
        trading: true,
        combat: true,
        colony: true,
        port: true
      }
    });
  }, []);

  // Fetch AI recommendations from the API with client-side fallback
  const fetchRecommendations = useCallback(async () => {
    try {
      const token = localStorage.getItem('accessToken');
      if (!token) {
        generateClientSideRecommendations();
        return;
      }

      const response = await fetch(`${API_BASE_URL}/api/v1/ai/recommendations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          system_types: selectedSystems.length > 0 ? selectedSystems : ['trading'],
          max_recommendations: 5
        })
      });

      if (response.ok) {
        const data = await response.json();
        if (Array.isArray(data) && data.length > 0) {
          setRecommendations(data);
          return;
        }
      }

      // If API returned empty or failed, try the trading-specific endpoint
      const tradingResponse = await fetch(`${API_BASE_URL}/ai/recommendations?limit=5`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (tradingResponse.ok) {
        const tradingData = await tradingResponse.json();
        if (Array.isArray(tradingData) && tradingData.length > 0) {
          // Map trading recommendations to the AIRecommendation format
          const mapped: AIRecommendation[] = tradingData.map((rec: any) => ({
            id: rec.id || String(Math.random()),
            category: 'trading' as const,
            recommendation_type: rec.type || 'trade_opportunity',
            title: rec.reasoning?.substring(0, 60) || 'Trading Opportunity',
            summary: rec.reasoning || 'A trading opportunity has been identified.',
            priority: rec.priority || 3,
            risk_assessment: (rec.risk_level || 'medium') as AIRecommendation['risk_assessment'],
            confidence: rec.confidence || 0.7,
            expected_outcome: {
              type: 'profit',
              value: rec.expected_profit || 0,
              currency: 'credits',
              probability: rec.confidence || 0.7
            },
            expires_at: rec.expires_at || new Date(Date.now() + 3600000).toISOString(),
            security_clearance_required: 'standard'
          }));
          setRecommendations(mapped);
          return;
        }
      }

      // If both API calls returned nothing, generate client-side recommendations
      generateClientSideRecommendations();
    } catch (error) {
      console.warn('Failed to fetch AI recommendations, using client-side fallback:', error);
      generateClientSideRecommendations();
    }
  }, [selectedSystems, API_BASE_URL]);

  // Generate simple client-side recommendations based on available context
  const generateClientSideRecommendations = useCallback(() => {
    const now = new Date();
    const expiresAt = new Date(now.getTime() + 3600000).toISOString();

    const contextualTips: AIRecommendation[] = [
      {
        id: `tip-trading-${now.getTime()}`,
        category: 'trading',
        recommendation_type: 'general_tip',
        title: 'Explore Nearby Trade Routes',
        summary: 'Check ports in adjacent sectors for price differences. Buying low and selling high across sectors is the fastest way to build credits.',
        priority: 3,
        risk_assessment: 'low',
        confidence: 0.8,
        expected_outcome: { type: 'profit', value: 5000, currency: 'credits', probability: 0.7 },
        expires_at: expiresAt,
        security_clearance_required: 'standard'
      },
      {
        id: `tip-combat-${now.getTime()}`,
        category: 'combat',
        recommendation_type: 'readiness_check',
        title: 'Check Your Ship Readiness',
        summary: 'Make sure your ship is repaired and stocked with drones before entering hostile sectors. A well-prepared ship survives longer.',
        priority: 2,
        risk_assessment: 'very_low',
        confidence: 0.9,
        expected_outcome: { type: 'survival', value: 1, currency: 'status' },
        expires_at: expiresAt,
        security_clearance_required: 'standard'
      },
      {
        id: `tip-strategic-${now.getTime()}`,
        category: 'strategic',
        recommendation_type: 'strategic_advice',
        title: 'Expand Your Influence',
        summary: 'Consider colonizing unowned planets to generate passive income and establish territorial control in key sectors.',
        priority: 2,
        risk_assessment: 'medium',
        confidence: 0.75,
        expected_outcome: { type: 'profit', value: 10000, currency: 'credits', probability: 0.5 },
        expires_at: expiresAt,
        security_clearance_required: 'standard'
      }
    ];

    // Filter tips based on selected systems
    const filtered = contextualTips.filter(tip =>
      selectedSystems.length === 0 || selectedSystems.includes(tip.category)
    );

    setRecommendations(filtered.slice(0, 3));
  }, [selectedSystems]);

  // Input sanitization
  const sanitizeInput = useCallback((input: string): string => {
    // Remove HTML tags and dangerous characters
    let sanitized = DOMPurify.sanitize(input, { ALLOWED_TAGS: [] });
    
    // Additional sanitization
    sanitized = sanitized.replace(/[<>\"'`]/g, '');
    sanitized = sanitized.replace(/javascript:|data:|vbscript:/gi, '');
    
    // Limit length
    return sanitized.slice(0, MAX_MESSAGE_LENGTH);
  }, []);

  // Send message to AI via WebSocket
  const sendMessage = useCallback(() => {
    if (!inputValue.trim() || isLoading || !checkRateLimit() || !isConnected) {
      if (!isConnected) {
        // Show connection warning
        setRateLimitWarning(true);
        setTimeout(() => setRateLimitWarning(false), 3000);
      }
      return;
    }
    
    const sanitizedMessage = sanitizeInput(inputValue.trim());
    if (!sanitizedMessage) {
      return;
    }
    
    setIsLoading(true);
    
    // Determine context based on selected systems
    let context = 'general';
    if (selectedSystems.length === 1) {
      context = selectedSystems[0];
    } else if (selectedSystems.includes('trading')) {
      context = 'trading';
    }
    
    // Send via WebSocket
    const success = sendARIAMessage(sanitizedMessage, conversationId, context);
    
    if (success) {
      setInputValue('');
      
      // Update conversation ID if not set
      if (!conversationId) {
        const newConversationId = `conv_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        setConversationId(newConversationId);
      }
    } else {
      console.error('Failed to send ARIA message via WebSocket');
    }
    
    // Set loading to false after a short delay (WebSocket response will handle it)
    setTimeout(() => setIsLoading(false), 5000); // 5-second timeout
  }, [inputValue, isLoading, checkRateLimit, sanitizeInput, conversationId, sendARIAMessage, selectedSystems, isConnected]);

  // Handle Enter key
  const handleKeyPress = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [sendMessage]);

  // Toggle voice input
  const toggleVoiceInput = useCallback(() => {
    if (!recognitionRef.current) {
      alert('Speech recognition is not supported in your browser.');
      return;
    }
    
    if (isListening) {
      recognitionRef.current.stop();
      setIsListening(false);
    } else {
      recognitionRef.current.start();
      setIsListening(true);
    }
  }, [isListening]);

  // Clear conversation
  const clearConversation = useCallback(() => {
    clearARIAMessages();
    setConversationId(null);
  }, [clearARIAMessages]);

  // Accept recommendation (simplified for WebSocket)
  const acceptRecommendation = useCallback(async (recommendation: AIRecommendation) => {
    try {
      // Remove from recommendations
      setRecommendations(prev => prev.filter(r => r.id !== recommendation.id));
      
      // Send acceptance message to ARIA
      const success = sendARIAMessage(`I accept your recommendation: ${recommendation.title}`, conversationId, 'trading');
      
      if (!success) {
        console.error('Failed to send acceptance message to ARIA');
      }
      
    } catch (error) {
      console.error('Failed to accept recommendation:', error);
    }
  }, [sendARIAMessage, conversationId]);

  // Get priority color
  const getPriorityColor = (priority: number): string => {
    switch (priority) {
      case 5: return 'rgb(239, 68, 68)'; // red-500
      case 4: return 'rgb(245, 101, 101)'; // red-400  
      case 3: return 'rgb(251, 146, 60)'; // orange-400
      case 2: return 'rgb(34, 197, 94)'; // green-500
      case 1: return 'rgb(156, 163, 175)'; // gray-400
      default: return 'rgb(251, 146, 60)'; // orange-400
    }
  };

  // Get risk color
  const getRiskColor = (risk: string): string => {
    switch (risk) {
      case 'very_high': return 'rgb(239, 68, 68)'; // red-500
      case 'high': return 'rgb(245, 101, 101)'; // red-400
      case 'medium': return 'rgb(251, 146, 60)'; // orange-400
      case 'low': return 'rgb(34, 197, 94)'; // green-500
      case 'very_low': return 'rgb(16, 185, 129)'; // emerald-500
      default: return 'rgb(251, 146, 60)'; // orange-400
    }
  };

  // Format currency
  const formatCurrency = (value: number): string => {
    return new Intl.NumberFormat('en-US', {
      style: 'decimal',
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(value);
  };

  if (isMinimized) {
    return (
      <div className="ai-assistant-minimized" onClick={onToggleMinimize}>
        <Brain className="w-6 h-6" />
        <span className="sr-only">Open ARIA AI Assistant</span>
      </div>
    );
  }

  if (!isOpen) {
    return (
      <button
        className="ai-assistant-trigger"
        onClick={() => setIsOpen(true)}
        aria-label="Open ARIA AI Assistant"
      >
        <Brain className="w-6 h-6" />
        <Zap className="w-4 h-4 ai-assistant-spark" />
      </button>
    );
  }

  return (
    <div className={`ai-assistant ${theme}`}>
      {/* Header */}
      <div className="ai-assistant-header">
        <div className="ai-assistant-title">
          <Brain className="w-5 h-5" />
          <span>ARIA</span>
          <div className="ai-assistant-status">
            {isConnected ? (
              <CheckCircle className="w-4 h-4 text-green-400" />
            ) : (
              <AlertTriangle className="w-4 h-4 text-yellow-400" />
            )}
          </div>
        </div>
        
        <div className="ai-assistant-actions">
          <button
            onClick={() => setShowSettings(!showSettings)}
            className="ai-assistant-action"
            aria-label="Settings"
          >
            <Settings className="w-4 h-4" />
          </button>
          
          {onToggleMinimize && (
            <button
              onClick={onToggleMinimize}
              className="ai-assistant-action"
              aria-label="Minimize"
            >
              <Minimize2 className="w-4 h-4" />
            </button>
          )}
          
          <button
            onClick={() => setIsOpen(false)}
            className="ai-assistant-action"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Settings Panel */}
      {showSettings && (
        <div className="ai-assistant-settings">
          <h3>AI Systems</h3>
          <div className="ai-system-toggles">
            {[
              { key: 'trading', label: 'Trading', icon: TrendingUp },
              { key: 'combat', label: 'Combat', icon: Shield },
              { key: 'colony', label: 'Colony', icon: Target },
              { key: 'port', label: 'Ports', icon: Star },
              { key: 'strategic', label: 'Strategic', icon: Brain }
            ].map(({ key, label, icon: Icon }) => (
              <label key={key} className="ai-system-toggle">
                <input
                  type="checkbox"
                  checked={selectedSystems.includes(key)}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setSelectedSystems(prev => [...prev, key]);
                    } else {
                      setSelectedSystems(prev => prev.filter(s => s !== key));
                    }
                  }}
                />
                <Icon className="w-4 h-4" />
                <span>{label}</span>
              </label>
            ))}
          </div>
          
          {assistantStatus && (
            <div className="ai-assistant-quota">
              <div className="quota-label">API Usage</div>
              <div className="quota-bar">
                <div 
                  className="quota-fill"
                  style={{ 
                    width: `${(assistantStatus.api_usage.used / assistantStatus.api_usage.quota) * 100}%` 
                  }}
                />
              </div>
              <div className="quota-text">
                {assistantStatus.api_usage.used} / {assistantStatus.api_usage.quota}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Recommendations Panel */}
      {recommendations.length > 0 && (
        <div className="ai-recommendations">
          <h3>
            <Star className="w-4 h-4" />
            AI Recommendations
          </h3>
          
          <div className="recommendations-list">
            {recommendations.slice(0, 3).map((rec) => (
              <div key={rec.id} className="recommendation-card">
                <div className="recommendation-header">
                  <div className="recommendation-title">{rec.title}</div>
                  <div className="recommendation-badges">
                    <span 
                      className="priority-badge"
                      style={{ backgroundColor: getPriorityColor(rec.priority) }}
                    >
                      P{rec.priority}
                    </span>
                    <span 
                      className="risk-badge"
                      style={{ backgroundColor: getRiskColor(rec.risk_assessment) }}
                    >
                      {rec.risk_assessment.replace('_', ' ').toUpperCase()}
                    </span>
                  </div>
                </div>
                
                <div className="recommendation-summary">{rec.summary}</div>
                
                <div className="recommendation-outcome">
                  {rec.expected_outcome.type === 'profit' && (
                    <span className="outcome-profit">
                      Expected: {formatCurrency(rec.expected_outcome.value)} credits
                    </span>
                  )}
                  <span className="confidence-score">
                    {Math.round(rec.confidence * 100)}% confidence
                  </span>
                </div>
                
                <div className="recommendation-actions">
                  <button
                    onClick={() => acceptRecommendation(rec)}
                    className="accept-recommendation"
                  >
                    Accept
                  </button>
                  <button
                    onClick={() => setInputValue(`Tell me more about: ${rec.title}`)}
                    className="learn-more"
                  >
                    Learn More
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="ai-messages">
        {ariaMessages.length === 0 && (
          <div className="ai-welcome">
            <Brain className="w-8 h-8" />
            <h3>Hello! I'm ARIA</h3>
            <p>Your AI assistant for strategic trading, combat tactics, colonization planning, and comprehensive strategic guidance.</p>
            <div className="ai-suggestions">
              {[
                "What's the best trade route right now?",
                "Help me plan my next strategic move",
                "Should I buy that port in sector 15?",
                "Analyze my combat readiness"
              ].map((suggestion, idx) => (
                <button
                  key={idx}
                  onClick={() => setInputValue(suggestion)}
                  className="ai-suggestion"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        
        {ariaMessages.map((message) => (
          <div key={message.id} className={`message ${message.type}`}>
            <div className="message-content">
              {message.content}
            </div>
            
            {message.type === 'ai' && (
              <div className="message-metadata">
                <Clock className="w-3 h-3" />
                <span>Real-time</span>
                {message.confidence && (
                  <>
                    <Target className="w-3 h-3" />
                    <span>{Math.round(message.confidence * 100)}%</span>
                  </>
                )}
              </div>
            )}
            
            {message.type === 'ai' && message.actions && message.actions.length > 0 && (
              <div className="message-actions">
                <h4>Recommendations:</h4>
                {message.actions.map((action, idx) => (
                  <div key={idx} className="action-suggestion">
                    <strong>{action.type}:</strong> {JSON.stringify(action)}
                  </div>
                ))}
              </div>
            )}
            
            {message.type === 'ai' && message.suggestions && message.suggestions.length > 0 && (
              <div className="message-suggestions">
                {message.suggestions.slice(0, 3).map((suggestion, idx) => (
                  <button
                    key={idx}
                    onClick={() => setInputValue(suggestion)}
                    className="suggestion-button"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        
        {isLoading && (
          <div className="message ai loading">
            <div className="message-content">
              <Loader className="w-4 h-4 animate-spin" />
              <span>ARIA is thinking...</span>
            </div>
          </div>
        )}
        
        <div ref={messagesEndRef} />
      </div>

      {/* Rate Limit Warning */}
      {rateLimitWarning && (
        <div className="rate-limit-warning">
          <AlertTriangle className="w-4 h-4" />
          <span>Please wait before sending another message</span>
        </div>
      )}

      {/* Input */}
      <div className="ai-input-container">
        <div className="ai-input-wrapper">
          <input
            ref={inputRef}
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder="Ask ARIA anything about your strategy..."
            maxLength={MAX_MESSAGE_LENGTH}
            className="ai-input"
            disabled={isLoading}
          />
          
          <div className="ai-input-actions">
            {recognitionRef.current && (
              <button
                onClick={toggleVoiceInput}
                className={`ai-input-action ${isListening ? 'listening' : ''}`}
                aria-label={isListening ? 'Stop listening' : 'Start voice input'}
                disabled={isLoading}
              >
                {isListening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
              </button>
            )}
            
            <button
              onClick={sendMessage}
              className="ai-input-action send"
              disabled={!inputValue.trim() || isLoading}
              aria-label="Send message"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
        
        <div className="ai-input-footer">
          <span className="character-count">
            {inputValue.length}/{MAX_MESSAGE_LENGTH}
          </span>
          
          {ariaMessages.length > 0 && (
            <button
              onClick={clearConversation}
              className="clear-conversation"
            >
              Clear Chat
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default EnhancedAIAssistant;