// NOTE (cockpit Law 4): the floating ARIA assistant (FAB + panel) is retired.
// ARIA now lives in the teleprinter (components/aria/Teleprinter.tsx) --
// AriaTerminalPage.tsx, its MFD-console predecessor, was deleted
// (WO-UI5-RETIREMENT+GLASS, zero remaining consumers). This directory keeps
// only the shared AI type definitions, which services/aiTradingService.ts
// imports directly from ./types.

// Type exports
export type { TradingRecommendation, MarketAnalysis, PlayerTradingProfile, AIPreferences } from './types';
