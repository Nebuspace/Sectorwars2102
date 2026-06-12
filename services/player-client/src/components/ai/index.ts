// NOTE (cockpit Law 4): the floating ARIA assistant (FAB + panel) is retired.
// ARIA now docks into the console as components/aria/AriaConsoleStrip.
// This directory keeps only the shared AI type definitions, which
// services/aiTradingService.ts imports directly from ./types.

// Type exports
export type { TradingRecommendation, MarketAnalysis, PlayerTradingProfile, AIPreferences } from './types';
