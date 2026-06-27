import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import './route-rail.css';

/* SHIP SYSTEMS rail — one key per console route, each carrying its Law-5
   accent so the active route lights in its own system color. Mnemonics
   replace the old emoji icons; the full label rides on title/aria-label
   so hover and screen readers keep the long name. */
const NAV_ITEMS: Array<{ to: string; mnemonic: string; label: string; accent: string }> = [
  { to: '/game', mnemonic: 'CMD', label: 'COMMAND', accent: '#00D9FF' },
  { to: '/game/map', mnemonic: 'NAV', label: 'NAV CHART', accent: '#00D9FF' },
  { to: '/game/ships', mnemonic: 'HGR', label: 'HANGAR', accent: '#9EC5FF' },
  { to: '/game/settings', mnemonic: 'SET', label: 'SETTINGS', accent: '#9AA7B4' },
  { to: '/game/planets', mnemonic: 'COL', label: 'COLONIES', accent: '#7B2FFF' },
  { to: '/game/combat', mnemonic: 'WPN', label: 'WEAPONS', accent: '#FF4D6D' },
  { to: '/game/team', mnemonic: 'CRW', label: 'CREW', accent: '#00FF7F' },
  { to: '/game/ranking', mnemonic: 'SVC', label: 'SERVICE RECORD', accent: '#FFD700' },
];

/* Navigation chrome, NOT a softkey rail: these are router Links, and the
   rail lives OUTSIDE any MFD page error boundary so route navigation
   survives a page fault (contract rule 4). */
const RouteRail: React.FC = () => {
  const location = useLocation();

  return (
    <nav className="route-rail" aria-label="Ship systems">
      <div className="rr-grid">
        {NAV_ITEMS.map((item) => {
          const isActive = location.pathname === item.to;
          return (
            <Link
              key={item.to}
              to={item.to}
              className={`rr-key${isActive ? ' rr-key-active' : ''}`}
              style={{ '--rr-accent': item.accent } as React.CSSProperties}
              title={item.label}
              aria-label={item.label}
              aria-current={isActive ? 'page' : undefined}
            >
              {item.mnemonic}
            </Link>
          );
        })}
      </div>
    </nav>
  );
};

export default RouteRail;
