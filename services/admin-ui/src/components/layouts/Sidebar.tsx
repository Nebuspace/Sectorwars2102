import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import LogoutButton from '../auth/LogoutButton';
import SystemHealthStatus from '../ui/SystemHealthStatus';
import LanguageSwitcher from '../common/LanguageSwitcher';

interface NavGroup {
  id: string;
  label: string;
  icon: string;
  items: Array<{
    to: string;
    label: string;
    icon: string;
  }>;
}

const Sidebar: React.FC = () => {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set(['universe', 'players']));

  const toggleGroup = (groupId: string) => {
    setExpandedGroups(prev => {
      const newSet = new Set(prev);
      if (newSet.has(groupId)) {
        newSet.delete(groupId);
      } else {
        newSet.add(groupId);
      }
      return newSet;
    });
  };

  const navGroups: NavGroup[] = [
    {
      id: 'universe',
      label: 'Universe Management',
      icon: '🌌',
      items: [
        { to: '/universe', label: 'Universe Overview', icon: '🪐' },
        { to: '/universe/bang', label: 'Bang Galaxy', icon: '💥' },
        { to: '/sectors', label: 'Sectors', icon: '🗺️' },
        { to: '/universe/planets', label: 'Planets', icon: '🏙️' },
        { to: '/colonies', label: 'Colonization', icon: '🚀' },
        { to: '/universe/stations', label: 'Stations', icon: '🏢' },
        { to: '/universe/warptunnels', label: 'Warp Tunnels', icon: '🌀' },
        { to: '/nexus', label: 'Central Nexus', icon: '🌟' }
      ]
    },
    {
      id: 'regional',
      label: 'Regional Governance',
      icon: '🏛️',
      items: [
        { to: '/regional-governor', label: 'Governor Dashboard', icon: '👑' }
      ]
    },
    {
      id: 'players',
      label: 'Player Management',
      icon: '👥',
      items: [
        { to: '/users', label: 'Users', icon: '👤' },
        { to: '/players', label: 'Players', icon: '🎮' },
        { to: '/teams', label: 'Teams', icon: '🤝' }
      ]
    },
    {
      id: 'operations',
      label: 'Game Operations',
      icon: '⚡',
      items: [
        { to: '/fleets', label: 'Fleets', icon: '🚀' },
        { to: '/combat', label: 'Combat', icon: '⚔️' },
        { to: '/contract-disputes', label: 'Contract Disputes', icon: '⚖️' },
        { to: '/events', label: 'Events', icon: '🎯' },
        { to: '/factions', label: 'Factions', icon: '🏴' }
      ]
    },
    {
      id: 'analytics',
      label: 'Analytics & AI',
      icon: '📊',
      items: [
        { to: '/analytics', label: 'Analytics', icon: '📈' },
        { to: '/economy', label: 'Economy', icon: '💰' },
        { to: '/ai-trading', label: 'AI Trading', icon: '🤖' }
      ]
    },
    {
      id: 'security',
      label: 'Security & Admin',
      icon: '🔐',
      items: [
        { to: '/security', label: 'Security', icon: '🔒' },
        { to: '/permissions', label: 'Permissions', icon: '🔑' },
        { to: '/first-login-conversations', label: 'First Login', icon: '💬' },
        { to: '/messages', label: 'Message Moderation', icon: '📨' },
        { to: '/multi-account', label: 'Multi-Account Review', icon: '🔍' },
        { to: '/translations', label: 'Translations', icon: '🌍' }
      ]
    }
  ];

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <h1 className="sidebar-title">Sector Wars</h1>
        <p className="sidebar-subtitle">Admin Panel</p>
      </div>
      
      <nav className="sidebar-nav">
        {/* Dashboard - always visible */}
        <NavLink 
          to="/dashboard" 
          className={({ isActive }) => `sidebar-nav-item ${isActive ? 'active' : ''}`}
        >
          <span className="sidebar-nav-icon">📊</span>
          <span>Dashboard</span>
        </NavLink>

        {/* Grouped navigation */}
        {navGroups.map((group) => (
          <div key={group.id} className="sidebar-nav-group">
            <button
              className={`sidebar-nav-group-header ${expandedGroups.has(group.id) ? 'expanded' : ''}`}
              onClick={() => toggleGroup(group.id)}
            >
              <span className="sidebar-nav-icon">{group.icon}</span>
              <span className="sidebar-nav-group-label">{group.label}</span>
              <span className="sidebar-nav-group-arrow">
                {expandedGroups.has(group.id) ? '▼' : '▶'}
              </span>
            </button>
            
            {expandedGroups.has(group.id) && (
              <div className="sidebar-nav-group-items">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) => `sidebar-nav-item sidebar-nav-subitem ${isActive ? 'active' : ''}`}
                  >
                    <span className="sidebar-nav-icon">{item.icon}</span>
                    <span>{item.label}</span>
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        ))}
      </nav>
      
      <div className="sidebar-footer">
        <div style={{ padding: 'var(--space-6)', display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          <SystemHealthStatus />
          <LanguageSwitcher />
          <LogoutButton />
        </div>
      </div>
    </aside>
  );
};

export default Sidebar;