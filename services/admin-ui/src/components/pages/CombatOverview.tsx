import React, { useState, useEffect, useCallback } from 'react';
import PageHeader from '../ui/PageHeader';
import { CombatActivityChart } from '../charts/CombatActivityChart';
import { CombatFeed } from '../combat/CombatFeed';
import { DisputePanel } from '../combat/DisputePanel';
import DroneOperationsTab from '../combat/DroneOperationsTab';
import BalanceAnalytics from '../combat/BalanceAnalytics';
import { api } from '../../utils/auth';
import { useCombatUpdates } from '../../contexts/WebSocketContext';
import './combat-overview.css';

// CombatEvent local type removed (NH10): it was a stale flat shape that did not
// match the nested backend payload (CombatFeedItem). combatEvents is now any[]
// — CombatFeed accepts any[] and reads the payload defensively.

interface CombatStats {
  timestamp: string | null;
  active_combats: {
    total: number;
    by_type: Record<string, number>;
    needing_intervention: number;
  };
  balance_summary: {
    score: number;
    total_combats_24h: number;
    outliers_count: number;
    top_recommendation: string;
  };
  dispute_summary: {
    total_disputes: number;
    by_severity: {
      critical: number;
      high: number;
      medium: number;
      low: number;
    };
    critical_disputes: any[];
  };
  recent_combats: any[];
}

interface CombatRanking {
  playerId: string;
  playerName: string;
  kills: number;
  deaths: number;
  kdRatio: number;
  damageDealt: number;
  rank?: number;
  faction?: string;
  winRate?: number;
  totalDamage: number;
}

// Matches backend CombatDisputeResponse in admin_combat.py
interface CombatDispute {
  id: string;
  combat_id: string | null;
  type: string;
  severity: string;
  timestamp: string;
  description: string;
  participants: Record<string, unknown>;
  status: string;
  recommended_action: string;
}

export const CombatOverview: React.FC = () => {
  const [combatEvents, setCombatEvents] = useState<any[]>([]);
  const [combatStats, setCombatStats] = useState<CombatStats | null>(null);
  const [rankings, setRankings] = useState<CombatRanking[]>([]);
  const [disputes, setDisputes] = useState<CombatDispute[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedView, setSelectedView] = useState<'feed' | 'disputes' | 'rankings'>('feed');
  const [showInterventionModal, setShowInterventionModal] = useState(false);
  const [selectedCombatId, setSelectedCombatId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  // WebSocket handlers
  const handleNewCombatEvent = useCallback((data: any) => {
    setCombatEvents(prev => [data, ...prev].slice(0, 100)); // Keep last 100 events
    setLastUpdate(new Date());
  }, []);

  const handleDisputeFiled = useCallback((data: any) => {
    setDisputes(prev => [data, ...prev]);
    setLastUpdate(new Date());
  }, []);

  const handleStatsUpdate = useCallback((data: any) => {
    setCombatStats(data);
    setLastUpdate(new Date());
  }, []);

  // Subscribe to WebSocket events
  useCombatUpdates(handleNewCombatEvent, handleDisputeFiled, handleStatsUpdate);

  const loadData = async () => {
    setIsLoading(true);
    setError(null);
    // Fetch all combat data concurrently - use allSettled so partial failures don't blank everything
    const [eventsRes, statsRes, logsRes, disputesRes] = await Promise.allSettled([
      api.get('/api/v1/admin/combat/live'),
      api.get('/api/v1/admin/combat/dashboard-summary'),
      api.get('/api/v1/admin/combat/logs', { params: { time_filter: '30d', limit: 1000 } }),
      api.get('/api/v1/admin/combat/disputes')
    ]);

    // Track errors for display
    const errors: string[] = [];

    // Process combat events
    if (eventsRes.status === 'fulfilled') {
      setCombatEvents(eventsRes.value.data);
    } else {
      setCombatEvents([]);
      errors.push('Combat feed unavailable');
    }

    // Process combat statistics
    if (statsRes.status === 'fulfilled') {
      setCombatStats(statsRes.value.data as CombatStats);
    } else {
      setCombatStats(null);
      errors.push('Combat statistics unavailable');
    }

    // Process combat logs into rankings
    if (logsRes.status === 'fulfilled') {
      const logs = logsRes.value.data as any[];

      // Aggregate player stats from combat logs
      const playerStats: Record<string, {
        playerId: string;
        playerName: string;
        kills: number;
        deaths: number;
        totalDamage: number;
        wins: number;
        totalFights: number;
        faction: string;
      }> = {};

      for (const log of logs) {
        const attackerName = log.attacker?.username || 'Unknown';
        const defenderName = log.defender?.username || 'Unknown';
        const attackerId = log.combat_id ? `attacker-${attackerName}` : attackerName;
        const defenderId = log.combat_id ? `defender-${defenderName}` : defenderName;

        // Initialize attacker stats
        if (attackerName !== 'Unknown') {
          if (!playerStats[attackerName]) {
            playerStats[attackerName] = {
              playerId: attackerId,
              playerName: attackerName,
              kills: 0, deaths: 0, totalDamage: 0,
              wins: 0, totalFights: 0, faction: 'Unknown'
            };
          }
          playerStats[attackerName].totalFights++;
          playerStats[attackerName].totalDamage += (log.damage_dealt?.attacker_damage || 0);
          if (log.outcome === 'attacker_win') {
            playerStats[attackerName].kills++;
            playerStats[attackerName].wins++;
          } else if (log.outcome === 'defender_win') {
            playerStats[attackerName].deaths++;
          }
        }

        // Initialize defender stats
        if (defenderName !== 'Unknown') {
          if (!playerStats[defenderName]) {
            playerStats[defenderName] = {
              playerId: defenderId,
              playerName: defenderName,
              kills: 0, deaths: 0, totalDamage: 0,
              wins: 0, totalFights: 0, faction: 'Unknown'
            };
          }
          playerStats[defenderName].totalFights++;
          playerStats[defenderName].totalDamage += (log.damage_dealt?.defender_damage || 0);
          if (log.outcome === 'defender_win') {
            playerStats[defenderName].kills++;
            playerStats[defenderName].wins++;
          } else if (log.outcome === 'attacker_win') {
            playerStats[defenderName].deaths++;
          }
        }
      }

      // Convert to rankings array sorted by kills desc
      const computedRankings: CombatRanking[] = Object.values(playerStats)
        .map(stats => ({
          playerId: stats.playerId,
          playerName: stats.playerName,
          kills: stats.kills,
          deaths: stats.deaths,
          kdRatio: stats.deaths > 0 ? stats.kills / stats.deaths : stats.kills,
          damageDealt: stats.totalDamage,
          totalDamage: stats.totalDamage,
          faction: stats.faction,
          winRate: stats.totalFights > 0 ? Math.round((stats.wins / stats.totalFights) * 100) : 0
        }))
        .sort((a, b) => b.kills - a.kills || b.kdRatio - a.kdRatio)
        .slice(0, 50);

      setRankings(computedRankings);
    } else {
      setRankings([]);
    }

    // Process disputes
    if (disputesRes.status === 'fulfilled') {
      setDisputes(disputesRes.value.data as CombatDispute[]);
    } else {
      setDisputes([]);
      errors.push('Combat disputes unavailable');
    }

    // Show combined error if all endpoints failed
    if (errors.length === 4) {
      setError('Failed to load combat data. Please check if the gameserver is running.');
    } else if (errors.length > 0) {
      setError(errors.join(' | '));
    }

    setIsLoading(false);
  };

  // Load initial data
  useEffect(() => {
    loadData();
    
    // Refresh data every 30 seconds
    const interval = setInterval(loadData, 30000);

    return () => clearInterval(interval);
  }, []);

  const handleDisputeClick = (_eventId: string) => {
    setSelectedView('disputes');
  };

  const handleInterventionClick = (eventId: string) => {
    setSelectedCombatId(eventId);
    setShowInterventionModal(true);
  };

  const handleIntervention = async (action: string) => {
    if (selectedCombatId) {
      try {
        const intervention_type =
          action === 'end' ? 'stop_combat' : action === 'restore' ? 'restore_shields' : null;
        if (!intervention_type) {
          setError('Unsupported intervention action');
          setShowInterventionModal(false);
          return;
        }
        await api.post(`/api/v1/admin/combat/${selectedCombatId}/intervene`, {
          intervention_type,
          parameters: {
            reason: `Admin intervention: ${action}`
          }
        });
        setShowInterventionModal(false);
        setSelectedCombatId(null);
        // Refresh data
        await loadData();
      } catch (error: any) {
        setError(error.response?.data?.detail || 'Failed to intervene in combat');
        setShowInterventionModal(false);
      }
    }
  };

  if (isLoading) {
    return (
      <div className="combat-overview loading">
        <PageHeader title="Combat Overview" />
        <div className="loading-spinner">Loading combat data...</div>
      </div>
    );
  }

  const activeBattles = combatStats?.active_combats?.total ?? 0;
  const needingIntervention = combatStats?.active_combats?.needing_intervention ?? 0;
  // Red alarm is for "needs attention" ONLY. A quiet battlefield (0 active
  // battles) or live battles with nothing needing intervention render neutral.
  const activeBattlesAlarm = activeBattles > 0 && needingIntervention > 0;

  return (
    <div className="combat-overview">
      <PageHeader title="Combat Overview" />
      
      {/* Real-time update indicator */}
      <div style={{ 
        display: 'flex', 
        justifyContent: 'flex-end', 
        marginBottom: '16px',
        fontSize: '12px',
        color: 'var(--text-secondary)'
      }}>
        <span>Last updated: {lastUpdate.toLocaleTimeString()}</span>
      </div>
      
      {/* Error Notice */}
      {error && (
        <div className="alert error" style={{ marginBottom: '20px' }}>
          <span className="alert-icon">❌</span>
          <span className="alert-message">
            {error}
          </span>
        </div>
      )}
      
      {/* View Selector */}
      <div className="view-selector">
        <button 
          className={`view-btn ${selectedView === 'feed' ? 'active' : ''}`}
          onClick={() => setSelectedView('feed')}
        >
          Live Feed
        </button>
        <button 
          className={`view-btn ${selectedView === 'disputes' ? 'active' : ''}`}
          onClick={() => setSelectedView('disputes')}
        >
          Disputes ({disputes.filter(d => d.status === 'pending').length})
        </button>
        <button 
          className={`view-btn ${selectedView === 'rankings' ? 'active' : ''}`}
          onClick={() => setSelectedView('rankings')}
        >
          Rankings
        </button>
      </div>

      {/* Content Area */}
      <div className="combat-content">
        {selectedView === 'feed' && (
          <CombatFeed 
            events={combatEvents}
            onDisputeClick={handleDisputeClick}
            onInterventionClick={handleInterventionClick}
          />
        )}
        
        {selectedView === 'disputes' && (
          <DisputePanel 
            disputes={disputes}
            onResolve={loadData}
          />
        )}
        
        {selectedView === 'rankings' && (
          <div className="combat-rankings">
            <h3>Combat Rankings</h3>
            <table className="rankings-table">
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Player</th>
                  <th>Faction</th>
                  <th>Kills</th>
                  <th>Deaths</th>
                  <th>K/D Ratio</th>
                  <th>Win Rate</th>
                  <th>Total Damage</th>
                </tr>
              </thead>
              <tbody>
                {rankings.length === 0 ? (
                  <tr>
                    <td colSpan={8} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-secondary)' }}>
                      No combat data available for rankings. Rankings are computed from the last 30 days of combat logs.
                    </td>
                  </tr>
                ) : (
                  rankings.map((player: CombatRanking, index: number) => (
                    <tr key={player.playerId}>
                      <td className="rank">#{index + 1}</td>
                      <td className="player-name">{player.playerName}</td>
                      <td className="faction">{player.faction || 'Unknown'}</td>
                      <td className="kills">{player.kills}</td>
                      <td className="deaths">{player.deaths}</td>
                      <td className="kd-ratio">{player.kdRatio.toFixed(2)}</td>
                      <td className="win-rate">{player.winRate || 0}%</td>
                      <td className="damage">{player.totalDamage.toLocaleString()}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Combat Statistics Dashboard */}
      {/* Page-unique .combat-stat-* class names: generic .stat-card/.stat-value
          names get clobbered by team-management-override.css's unscoped
          !important globals, which nullified the alarm styling. */}
      <div className="combat-stats-grid">
        {/* Alarm styling only when there are live battles needing intervention;
            a quiet battlefield is neutral, not red. */}
        <div className={`combat-stat-card${activeBattlesAlarm ? ' alarm' : ''}`}>
          <h3>Active Battles</h3>
          <div className="combat-stat-value">{activeBattles.toLocaleString()}</div>
          <div className="combat-stat-change">{needingIntervention.toLocaleString()} need intervention</div>
        </div>

        <div className="combat-stat-card">
          <h3>24h Battles</h3>
          <div className="combat-stat-value">
            {combatStats?.balance_summary?.total_combats_24h != null
              ? combatStats.balance_summary.total_combats_24h.toLocaleString()
              : '—'}
          </div>
          <div className="combat-stat-label">battles today</div>
        </div>

        <div className="combat-stat-card">
          <h3>Balance Score</h3>
          <div className="combat-stat-value">
            {combatStats?.balance_summary?.score != null
              ? `${combatStats.balance_summary.score.toFixed(0)}%`
              : '—'}
          </div>
          <div className="combat-stat-label">system balance</div>
        </div>

        <div className="combat-stat-card">
          <h3>Total Disputes</h3>
          <div className="combat-stat-value">
            {combatStats?.dispute_summary?.total_disputes != null
              ? combatStats.dispute_summary.total_disputes.toLocaleString()
              : '—'}
          </div>
          <div className="combat-stat-label">pending review</div>
        </div>

        <div className="combat-stat-card highlight">
          <h3>Critical Disputes</h3>
          <div className="combat-stat-value">
            {combatStats?.dispute_summary?.by_severity?.critical != null
              ? combatStats.dispute_summary.by_severity.critical
              : '—'}
          </div>
          <div className="combat-stat-label">need attention</div>
        </div>

        <div className="combat-stat-card highlight">
          <h3>Balance Outliers</h3>
          <div className="combat-stat-value">
            {combatStats?.balance_summary?.outliers_count != null
              ? combatStats.balance_summary.outliers_count
              : '—'}
          </div>
          <div className="combat-stat-label">imbalanced</div>
        </div>
      </div>

      {/* Combat Activity Chart */}
      <div className="combat-chart-section">
        <CombatActivityChart events={combatEvents} width={1200} height={300} />
      </div>

      {/* Intervention Modal */}
      {showInterventionModal && (
        <div className="modal-overlay" onClick={() => setShowInterventionModal(false)}>
          <div className="intervention-modal" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <h3>Combat Intervention</h3>
            <p>Select intervention action for combat: {selectedCombatId}</p>
            
            <div className="intervention-options">
              <button 
                className="btn btn-danger"
                onClick={() => handleIntervention('end')}
              >
                Force End Combat
              </button>
              
              <button 
                className="btn btn-success"
                onClick={() => handleIntervention('restore')}
              >
                Restore Ships
              </button>
            </div>
            <p
              role="note"
              style={{
                margin: '12px 0 0 0',
                padding: '8px 10px',
                background: 'rgba(234, 179, 8, 0.12)',
                border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px',
                color: '#fbbf24',
                fontSize: '0.82rem',
                lineHeight: 1.4,
              }}
            >
              This modal offers Force End (stop_combat) and Restore Ships (restore_shields)
              only. Pause, Reset, adjust_damage, and declare_winner controls are not shown —
              do not invent them here.
            </p>
            
            <button 
              className="btn btn-secondary"
              onClick={() => setShowInterventionModal(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Drone Operations command center */}
      <section className="drone-operations-section">
        <h2 className="drone-operations-section-title">Drone Operations</h2>
        <DroneOperationsTab />
      </section>

      {/* Combat Balance Analytics */}
      <section className="drone-operations-section">
        <h2 className="drone-operations-section-title">Balance Analytics</h2>
        <BalanceAnalytics />
      </section>
    </div>
  );
};

export default CombatOverview;