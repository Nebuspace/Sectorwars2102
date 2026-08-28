// AI Trading System Types

export interface TradingRecommendation {
  id: string;
  type: 'buy' | 'sell' | 'route' | 'avoid' | 'wait';
  commodity_id?: string;
  sector_id?: string;
  target_price?: number;
  expected_profit?: number;
  confidence: number; // 0-1
  risk_level: 'low' | 'medium' | 'high';
  reasoning: string;
  priority: number; // 1-5
  expires_at: string;
}

export interface MarketAnalysis {
  commodity_id: string;
  current_price: number;
  predicted_price: number;
  price_trend: 'rising' | 'falling' | 'stable';
  volatility: number;
  confidence: number;
  factors: string[];
  time_horizon: number; // hours
}

/** ADR-0068 / aria-companion.md — volunteering only, not learning depth. */
export const AI_ASSISTANCE_LEVELS = ['minimal', 'quiet', 'standard', 'full'] as const;
export type AIAssistanceLevel = (typeof AI_ASSISTANCE_LEVELS)[number];

export interface PlayerTradingProfile {
  player_id: string;
  risk_tolerance: number; // 0-1
  ai_assistance_level: AIAssistanceLevel;
  average_profit_per_trade: number;
  total_trades_analyzed: number;
  preferred_commodities?: Record<string, number>;
  trading_patterns?: Record<string, any>;
  performance_metrics?: Record<string, any>;
  last_active_sector?: string;
}

export interface AIPreferences {
  ai_assistance_level: AIAssistanceLevel;
  risk_tolerance: number;
  notification_preferences?: {
    market_opportunities?: boolean;
    risk_warnings?: boolean;
    price_alerts?: boolean;
    route_suggestions?: boolean;
  };
}

export interface RecommendationFeedback {
  accepted: boolean;
  feedback_score?: number; // 1-5
  feedback_text?: string;
}

export interface OptimalRoute {
  sectors: string[];
  total_profit: number;
  total_distance: number;
  estimated_time: number; // minutes
  risk_score: number;
  commodity_chain: Array<{
    sector: string;
    commodity: string;
    action: 'buy' | 'sell';
    price: number;
    quantity: number;
  }>;
}

export interface AIPerformanceStats {
  average_accuracy: number;
  average_user_satisfaction: number;
  total_predictions: number;
  performance_trend: 'improving' | 'stable' | 'declining';
  daily_performance: Array<{
    date: string;
    accuracy: number;
    predictions: number;
  }>;
}