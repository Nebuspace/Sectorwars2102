import React from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { useWebSocket } from '../../contexts/WebSocketContext';
import { ariaFeed } from '../mfd/ariaFeedStore';

/**
 * WelcomeBackToast — surfaces the returning-player turn-bonus grant
 * (WO-PUX-WBACK-SURFACE) as one cockpit toast + one ARIA feed line.
 *
 * AuthContext sits OUTSIDE WebSocketProvider in the tree (WebSocketContext
 * itself consumes useAuth — see App.tsx), so `login()` cannot call
 * `addNotification` directly. It instead exposes a monotonic
 * `welcomeBackSignal` + the outcome payload; this component is mounted
 * inside WebSocketProvider (GameLayout, alongside MedalToast/PriorityHail-
 * Consumer) purely to bridge that gap.
 *
 * One toast + one ARIA line per signal bump, never on mount (signal 0 is the
 * baseline, mirroring MedalToast's `medalAwardedSignal` guard). No client-
 * side dedupe beyond that: one-shot semantics are server-guaranteed
 * (`welcome_back()`'s `last_game_login` overwrite makes a second login
 * within the window grant nothing), so a signal bump here can only ever mean
 * a genuine new grant. Renders nothing.
 */
const WelcomeBackToast: React.FC = () => {
  const { welcomeBackSignal, lastWelcomeBack } = useAuth();
  const { addNotification } = useWebSocket();
  const prevSignal = React.useRef(welcomeBackSignal);

  React.useEffect(() => {
    if (welcomeBackSignal <= 0 || welcomeBackSignal === prevSignal.current) return;
    prevSignal.current = welcomeBackSignal;
    if (!lastWelcomeBack?.granted) return;

    const { bonus, days_inactive: daysInactive } = lastWelcomeBack;
    const dayWord = daysInactive === 1 ? 'day' : 'days';
    // Copy is NO-CANON (flagged for design sign-off) — hardcoded en text
    // since I18N-CORE hasn't landed yet.
    addNotification({
      title: 'Welcome Back',
      content: `+${bonus} turns — welcome back, Commander (${daysInactive} ${dayWord} away)`,
      level: 'success',
    });
    ariaFeed.appendNav(
      `Welcome back, Commander. +${bonus} turns credited — ${daysInactive} ${dayWord} away.`
    );
  }, [welcomeBackSignal, lastWelcomeBack, addNotification]);

  return null;
};

export default WelcomeBackToast;
