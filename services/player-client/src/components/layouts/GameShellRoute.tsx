import React from 'react';
import { Outlet } from 'react-router-dom';
import { useFirstLogin } from '../../contexts/FirstLoginContext';
import GameLayout from './GameLayout';

/**
 * GameShellRoute — the persistent-shell layout-route element for every
 * /game/* route (WO-UI0-PERSISTENT-SHELL lane A). App.tsx nests all 11
 * /game/* pages under ONE <Route path="/game" element={<GameShellRoute />}>
 * so <GameLayout> mounts exactly once and survives every /game/*
 * navigation — only the page in the Outlet slot swaps, never the shell
 * chrome around it (sidebar, HUD, toasts, MFDs).
 *
 * GameLayout itself sets ShellPresenceContext=true around the Outlet (see
 * ShellContext.ts), so a page component that still self-wraps its own
 * <GameLayout> (lane C not done yet — see the 11 page components) reads
 * shellPresent=true and no-ops that inner wrapper instead of nesting a
 * second cockpit chrome. Exactly one shell renders either way, which is
 * what makes nesting the route here safe before lane C unwraps the pages.
 *
 * First-login gate: mirrors the pre-lane-A behavior, where GameDashboard's
 * own requiresFirstLogin check returned null (no shell) while a fresh
 * account was still gated behind FirstLoginContainer's interrogation-booth
 * overlay (an unrelated, always-mounted sibling of <Routes> in App.tsx,
 * unchanged by this lane). A bare <Outlet /> keeps that contract: no live
 * shell content ever renders behind the overlay.
 */
function GameShellRoute() {
  const { requiresFirstLogin } = useFirstLogin();
  if (requiresFirstLogin) return <Outlet />;
  return (
    <GameLayout>
      <Outlet />
    </GameLayout>
  );
}

export default GameShellRoute;
