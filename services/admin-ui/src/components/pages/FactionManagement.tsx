import React, { useState, useEffect, useCallback, useMemo } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import './faction-management.css';

interface Faction {
  id: string;
  name: string;
  faction_type: string;
  description: string | null;
  territory_sectors: string[];
  home_sector_id: string | null;
  base_pricing_modifier: number;
  trade_specialties: string[];
  aggression_level: number;
  diplomacy_stance: string;
  color_primary: string | null;
  color_secondary: string | null;
  logo_url: string | null;
  created_at: string;
  updated_at: string;
}

interface FactionMission {
  id: string;
  faction_id: string;
  faction_name: string;
  title: string;
  mission_type: string;
  credit_reward: number;
  reputation_reward: number;
  min_reputation: number;
  is_active: boolean;
  expires_at: string | null;
  created_at: string;
}

const formatType = (value: string): string =>
  value
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');

const aggressionLevelClass = (level: number): string => {
  if (level >= 8) return 'aggression-high';
  if (level >= 5) return 'aggression-medium';
  return 'aggression-low';
};

const FactionManagement: React.FC = () => {
  const [factions, setFactions] = useState<Faction[]>([]);
  const [missions, setMissions] = useState<FactionMission[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [missionsError, setMissionsError] = useState<string | null>(null);

  const [searchTerm, setSearchTerm] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [stanceFilter, setStanceFilter] = useState<string>('all');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    setMissionsError(null);

    const [factionsResult, missionsResult] = await Promise.allSettled([
      api.get<Faction[]>('/api/v1/admin/factions/'),
      api.get<FactionMission[]>('/api/v1/admin/factions/missions/all?active_only=true'),
    ]);

    if (factionsResult.status === 'fulfilled') {
      setFactions(factionsResult.value.data ?? []);
    } else {
      console.error('Error fetching factions:', factionsResult.reason);
      setError('Failed to load factions.');
      setFactions([]);
    }

    if (missionsResult.status === 'fulfilled') {
      setMissions(missionsResult.value.data ?? []);
    } else {
      console.error('Error fetching faction missions:', missionsResult.reason);
      setMissionsError('Failed to load active missions.');
      setMissions([]);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const factionTypes = useMemo(() => {
    const set = new Set<string>();
    factions.forEach((f) => set.add(f.faction_type));
    return Array.from(set).sort();
  }, [factions]);

  const stances = useMemo(() => {
    const set = new Set<string>();
    factions.forEach((f) => set.add(f.diplomacy_stance));
    return Array.from(set).sort();
  }, [factions]);

  const filteredFactions = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    return factions.filter((f) => {
      const matchesSearch =
        term === '' ||
        f.name.toLowerCase().includes(term) ||
        (f.description ?? '').toLowerCase().includes(term);
      const matchesType = typeFilter === 'all' || f.faction_type === typeFilter;
      const matchesStance = stanceFilter === 'all' || f.diplomacy_stance === stanceFilter;
      return matchesSearch && matchesType && matchesStance;
    });
  }, [factions, searchTerm, typeFilter, stanceFilter]);

  const summary = useMemo(() => {
    const totalTerritory = factions.reduce((sum, f) => sum + f.territory_sectors.length, 0);
    const avgAggression =
      factions.length > 0
        ? factions.reduce((sum, f) => sum + f.aggression_level, 0) / factions.length
        : 0;
    const hostileCount = factions.filter((f) => f.diplomacy_stance === 'hostile').length;
    return {
      totalFactions: factions.length,
      totalTerritory,
      avgAggression,
      hostileCount,
      activeMissions: missions.length,
    };
  }, [factions, missions]);

  if (loading) {
    return (
      <div className="faction-management">
        <PageHeader
          title="Faction Management"
          subtitle="Monitor factions, territory, diplomacy, and active missions"
        />
        <div className="faction-loading">
          <div className="loading-spinner" />
          <span>Loading faction data...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="faction-management">
      <PageHeader
        title="Faction Management"
        subtitle="Monitor factions, territory, diplomacy, and active missions"
      />

      {error && (
        <div className="faction-error">
          <span>{error}</span>
          <button type="button" className="faction-btn" onClick={fetchData}>
            Retry
          </button>
        </div>
      )}

      {/* Summary stat row */}
      <div className="faction-stats-grid">
        <div className="faction-stat-card">
          <span className="faction-stat-label">Factions</span>
          <span className="faction-stat-value">{summary.totalFactions.toLocaleString()}</span>
        </div>
        <div className="faction-stat-card">
          <span className="faction-stat-label">Controlled Sectors</span>
          <span className="faction-stat-value">{summary.totalTerritory.toLocaleString()}</span>
        </div>
        <div className="faction-stat-card">
          <span className="faction-stat-label">Avg Aggression</span>
          <span className="faction-stat-value">{summary.avgAggression.toFixed(1)}</span>
        </div>
        <div className="faction-stat-card">
          <span className="faction-stat-label">Hostile Factions</span>
          <span className="faction-stat-value faction-stat-danger">{summary.hostileCount}</span>
        </div>
        <div className="faction-stat-card">
          <span className="faction-stat-label">Active Missions</span>
          <span className="faction-stat-value">{summary.activeMissions.toLocaleString()}</span>
        </div>
      </div>

      {/* Filters */}
      <div className="faction-controls">
        <input
          type="text"
          className="faction-search"
          placeholder="Search by name or description..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
        />
        <select
          className="faction-select"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="all">All Types</option>
          {factionTypes.map((t) => (
            <option key={t} value={t}>
              {formatType(t)}
            </option>
          ))}
        </select>
        <select
          className="faction-select"
          value={stanceFilter}
          onChange={(e) => setStanceFilter(e.target.value)}
        >
          <option value="all">All Stances</option>
          {stances.map((s) => (
            <option key={s} value={s}>
              {formatType(s)}
            </option>
          ))}
        </select>
        <button type="button" className="faction-btn" onClick={fetchData}>
          Refresh
        </button>
      </div>

      {/* Faction table */}
      <div className="faction-table-section">
        <h3 className="faction-section-title">Factions</h3>
        {filteredFactions.length === 0 ? (
          <div className="faction-empty">
            {factions.length === 0
              ? 'No factions found.'
              : 'No factions match the current filters.'}
          </div>
        ) : (
          <div className="faction-table-container">
            <table className="faction-table">
              <thead>
                <tr>
                  <th>Faction</th>
                  <th>Type</th>
                  <th>Territory</th>
                  <th>Aggression</th>
                  <th>Diplomacy</th>
                  <th>Pricing</th>
                  <th>Specialties</th>
                </tr>
              </thead>
              <tbody>
                {filteredFactions.map((faction) => (
                  <tr key={faction.id}>
                    <td>
                      <div className="faction-name-cell">
                        <span
                          className="faction-color-chip"
                          style={{
                            background: faction.color_primary ?? 'var(--border-medium)',
                          }}
                          aria-hidden="true"
                        />
                        <div className="faction-name-meta">
                          <span className="faction-name">{faction.name}</span>
                          {faction.description && (
                            <span className="faction-desc">{faction.description}</span>
                          )}
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="faction-type-badge">
                        {formatType(faction.faction_type)}
                      </span>
                    </td>
                    <td className="faction-mono">
                      {faction.territory_sectors.length.toLocaleString()}
                      {faction.home_sector_id && (
                        <span className="faction-home" title="Has home sector">
                          {' '}
                          ⌂
                        </span>
                      )}
                    </td>
                    <td>
                      <span
                        className={`faction-aggression ${aggressionLevelClass(
                          faction.aggression_level
                        )}`}
                      >
                        {faction.aggression_level}/10
                      </span>
                    </td>
                    <td>
                      <span
                        className={`faction-stance faction-stance-${faction.diplomacy_stance.toLowerCase()}`}
                      >
                        {formatType(faction.diplomacy_stance)}
                      </span>
                    </td>
                    <td className="faction-mono">
                      {faction.base_pricing_modifier.toFixed(2)}x
                    </td>
                    <td>
                      {faction.trade_specialties.length === 0 ? (
                        <span className="faction-muted">—</span>
                      ) : (
                        <div className="faction-specialties">
                          {faction.trade_specialties.map((s) => (
                            <span key={s} className="faction-specialty-tag">
                              {s}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Active missions */}
      <div className="faction-table-section">
        <h3 className="faction-section-title">Active Faction Missions</h3>
        {missionsError ? (
          <div className="faction-empty">{missionsError}</div>
        ) : missions.length === 0 ? (
          <div className="faction-empty">No active missions across factions.</div>
        ) : (
          <div className="faction-table-container">
            <table className="faction-table">
              <thead>
                <tr>
                  <th>Mission</th>
                  <th>Faction</th>
                  <th>Type</th>
                  <th>Credit Reward</th>
                  <th>Reputation</th>
                  <th>Min Reputation</th>
                </tr>
              </thead>
              <tbody>
                {missions.map((mission) => (
                  <tr key={mission.id}>
                    <td className="faction-name">{mission.title}</td>
                    <td>{mission.faction_name}</td>
                    <td>
                      <span className="faction-type-badge">
                        {formatType(mission.mission_type)}
                      </span>
                    </td>
                    <td className="faction-mono">
                      {mission.credit_reward.toLocaleString()} cr
                    </td>
                    <td className="faction-mono">{mission.reputation_reward}</td>
                    <td className="faction-mono">{mission.min_reputation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default FactionManagement;
