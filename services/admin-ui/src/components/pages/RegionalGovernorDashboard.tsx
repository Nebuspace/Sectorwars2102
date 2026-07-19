import React, { useState, useEffect } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import './regional-governor-dashboard.css';

interface Region {
  id: string;
  name: string;
  display_name: string;
  owner_id: string;
  subscription_tier: string;
  status: string;
  governance_type: string;
  tax_rate: number;
  voting_threshold: number;
  governance_quorum_pct?: number;
  economic_specialization: string;
  total_sectors: number;
  active_players_30d: number;
  total_trade_volume: number;
  starting_credits: number;
  starting_ship: string;
  constitutional_text?: string;
  language_pack: Record<string, string>;
  aesthetic_theme: Record<string, any>;
  trade_bonuses: Record<string, number>;
}

interface RegionalMember {
  player_id: string;
  username: string;
  membership_type: string;
  reputation_score: number;
  local_rank: string | null;
  voting_power: number;
  joined_at: string;
  last_visit: string;
  total_visits: number;
}

// Canon citizen-tier voting_power target (SYSTEMS/regional-governance.md:71-76).
const CITIZEN_DEFAULT_VOTING_POWER = 1.5;

interface RegionalStats {
  total_population: number;
  citizen_count: number;
  resident_count: number;
  visitor_count: number;
  average_reputation: number;
  total_revenue: number;
  trade_volume_30d: number;
  active_elections: number;
  pending_policies: number;
  treaties_count: number;
  planets_count: number;
  stations_count: number;
  ships_count: number;
}

interface Policy {
  id: string;
  policy_type: string;
  title: string;
  description: string;
  proposed_changes: Record<string, any>;
  proposed_by: string;
  proposed_at: string;
  voting_closes_at: string;
  votes_for: number;
  votes_against: number;
  status: string;
  approval_percentage: number;
}

interface Election {
  id: string;
  position: string;
  candidates: Array<{
    player_id: string;
    player_name: string;
    platform: string;
    vote_count?: number;
  }>;
  voting_opens_at: string;
  voting_closes_at: string;
  status: string;
}

interface Treaty {
  id: string;
  region_a_name: string;
  region_b_name: string;
  treaty_type: string;
  terms: Record<string, any>;
  signed_at: string;
  expires_at?: string;
  status: string;
}

const RegionalGovernorDashboard: React.FC = () => {
  const { token, user } = useAuth();
  const [region, setRegion] = useState<Region | null>(null);
  const [allRegions, setAllRegions] = useState<Region[]>([]);
  const [stats, setStats] = useState<RegionalStats | null>(null);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [elections, setElections] = useState<Election[]>([]);
  const [treaties, setTreaties] = useState<Treaty[]>([]);
  const [members, setMembers] = useState<RegionalMember[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'governance' | 'economy' | 'policies' | 'elections' | 'diplomacy' | 'culture' | 'members'>('overview');
  const isAdmin = user?.is_admin || false;

  // Policy creation state
  const [showPolicyForm, setShowPolicyForm] = useState(false);
  const [newPolicy, setNewPolicy] = useState({
    policy_type: 'tax_rate',
    title: '',
    description: '',
    proposed_changes: {}
  });

  // Economic configuration state
  const [economicConfig, setEconomicConfig] = useState({
    tax_rate: 0.10,
    starting_credits: 1000,
    trade_bonuses: {} as Record<string, number>,
    economic_specialization: ''
  });

  // Governance configuration state
  const [governanceConfig, setGovernanceConfig] = useState({
    governance_type: 'autocracy',
    voting_threshold: 0.51,
    election_frequency_days: 90,
    constitutional_text: '',
    governance_quorum_pct: 0.33
  });

  useEffect(() => {
    loadRegionalData();
  }, []);

  const loadRegionalData = async () => {
    setLoading(true);
    try {
      await Promise.all([
        loadRegionInfo(),
        loadRegionalStats(),
        loadPolicies(),
        loadElections(),
        loadTreaties(),
        loadMembers()
      ]);
    } catch (err) {
      setError('Failed to load regional data');
      console.error('Load error:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadRegionInfo = async () => {
    try {
      // First try to load the user's own region
      const response = await fetch('/api/v1/regions/my-region', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setRegion(data);
        setEconomicConfig({
          tax_rate: data.tax_rate,
          starting_credits: data.starting_credits,
          trade_bonuses: data.trade_bonuses || {},
          economic_specialization: data.economic_specialization || ''
        });
        setGovernanceConfig({
          governance_type: data.governance_type,
          voting_threshold: data.voting_threshold,
          election_frequency_days: data.election_frequency_days || 90,
          constitutional_text: data.constitutional_text || '',
          governance_quorum_pct: data.governance_quorum_pct ?? 0.33
        });
        return;
      }

      // If the user doesn't own a region but is admin, fetch all regions
      if (isAdmin) {
        const adminResponse = await fetch('/api/v1/admin/regions', {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        if (adminResponse.ok) {
          const adminData = await adminResponse.json();
          const regions = adminData.regions || [];
          setAllRegions(regions);
          if (regions.length > 0) {
            // Use the first region as default view
            const firstRegion = regions[0];
            setRegion({
              ...firstRegion,
              owner_id: firstRegion.owner_id || '',
              subscription_tier: firstRegion.subscription_tier || 'free',
              voting_threshold: firstRegion.voting_threshold || 0.51,
              economic_specialization: firstRegion.economic_specialization || '',
              active_players_30d: firstRegion.active_players_30d || 0,
              total_trade_volume: firstRegion.total_trade_volume || 0,
              starting_ship: firstRegion.starting_ship || 'basic',
              constitutional_text: firstRegion.constitutional_text || '',
              language_pack: firstRegion.language_pack || {},
              aesthetic_theme: firstRegion.aesthetic_theme || {},
              trade_bonuses: firstRegion.trade_bonuses || {},
            });
            setEconomicConfig({
              tax_rate: firstRegion.tax_rate || 0.10,
              starting_credits: firstRegion.starting_credits || 1000,
              trade_bonuses: firstRegion.trade_bonuses || {},
              economic_specialization: firstRegion.economic_specialization || ''
            });
            setGovernanceConfig({
              governance_type: firstRegion.governance_type || 'autocracy',
              voting_threshold: firstRegion.voting_threshold || 0.51,
              election_frequency_days: firstRegion.election_frequency_days || 90,
              constitutional_text: firstRegion.constitutional_text || '',
              governance_quorum_pct: firstRegion.governance_quorum_pct ?? 0.33
            });
          }
        }
      }
    } catch (err) {
      console.error('Failed to load region info:', err);
    }
  };

  const loadRegionalStats = async () => {
    try {
      const response = await fetch('/api/v1/regions/my-region/stats', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (err) {
      console.error('Failed to load regional stats:', err);
    }
  };

  const loadPolicies = async () => {
    try {
      const response = await fetch('/api/v1/regions/my-region/policies', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setPolicies(data);
      }
    } catch (err) {
      console.error('Failed to load policies:', err);
    }
  };

  const loadElections = async () => {
    try {
      const response = await fetch('/api/v1/regions/my-region/elections', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setElections(data);
      }
    } catch (err) {
      console.error('Failed to load elections:', err);
    }
  };

  const loadTreaties = async () => {
    try {
      const response = await fetch('/api/v1/regions/my-region/treaties', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setTreaties(data);
      }
    } catch (err) {
      console.error('Failed to load treaties:', err);
    }
  };

  const loadMembers = async () => {
    try {
      const response = await fetch('/api/v1/regions/my-region/members', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (response.ok) {
        const data = await response.json();
        setMembers(data);
      }
    } catch (err) {
      console.error('Failed to load regional members:', err);
    }
  };

  const updateEconomicConfig = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/regions/my-region/economy', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(economicConfig)
      });

      if (response.ok) {
        setSuccess('Economic configuration updated successfully');
        await loadRegionInfo();
      } else {
        const error = await response.json();
        setError(error.detail || 'Failed to update economic configuration');
      }
    } catch (err) {
      setError('Network error occurred');
      console.error('Update error:', err);
    } finally {
      setLoading(false);
    }
  };

  const updateGovernanceConfig = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/regions/my-region/governance', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(governanceConfig)
      });

      if (response.ok) {
        setSuccess('Governance configuration updated successfully');
        await loadRegionInfo();
      } else {
        const error = await response.json();
        setError(error.detail || 'Failed to update governance configuration');
      }
    } catch (err) {
      setError('Network error occurred');
      console.error('Update error:', err);
    } finally {
      setLoading(false);
    }
  };

  const createPolicy = async () => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/regions/my-region/policies', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(newPolicy)
      });

      if (response.ok) {
        setSuccess('Policy proposal created successfully');
        setShowPolicyForm(false);
        setNewPolicy({
          policy_type: 'tax_rate',
          title: '',
          description: '',
          proposed_changes: {}
        });
        await loadPolicies();
      } else {
        const error = await response.json();
        setError(error.detail || 'Failed to create policy');
      }
    } catch (err) {
      setError('Network error occurred');
      console.error('Create policy error:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleMemberFieldChange = (playerId: string, field: 'voting_power' | 'local_rank', value: string | number) => {
    setMembers(prev => prev.map(m => m.player_id === playerId ? { ...m, [field]: value } : m));
  };

  const updateMemberDials = async (playerId: string) => {
    const member = members.find(m => m.player_id === playerId);
    if (!member) return;

    setLoading(true);
    try {
      const response = await fetch(`/api/v1/regions/my-region/members/${playerId}`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          voting_power: member.voting_power,
          local_rank: member.local_rank || null
        })
      });

      if (response.ok) {
        setSuccess(`Updated governance dials for ${member.username}`);
        await loadMembers();
      } else {
        const error = await response.json();
        setError(error.detail || 'Failed to update member dials');
      }
    } catch (err) {
      setError('Network error occurred');
      console.error('Update member dials error:', err);
    } finally {
      setLoading(false);
    }
  };

  const startElection = async (position: string) => {
    setLoading(true);
    try {
      const response = await fetch('/api/v1/regions/my-region/elections', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({
          position,
          voting_duration_days: 7
        })
      });

      if (response.ok) {
        setSuccess(`Election for ${position} started successfully`);
        await loadElections();
      } else {
        const error = await response.json();
        setError(error.detail || 'Failed to start election');
      }
    } catch (err) {
      setError('Network error occurred');
      console.error('Start election error:', err);
    } finally {
      setLoading(false);
    }
  };

  const formatNumber = (num: number | null | undefined) => {
    if (num == null || Number.isNaN(num)) return '—';
    return num.toLocaleString();
  };

  const formatCurrency = (amount: number | null | undefined) => {
    if (amount == null || Number.isNaN(amount)) return '—';
    return `${amount.toLocaleString()} credits`;
  };

  const formatPercentage = (value: number) => {
    return `${(value * 100).toFixed(1)}%`;
  };

  const getStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
      case 'active': return 'status-active';
      case 'voting': return 'status-voting';
      case 'passed': return 'status-passed';
      case 'rejected': return 'status-rejected';
      case 'pending': return 'status-pending';
      default: return 'status-unknown';
    }
  };

  const getPolicyTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      'tax_rate': 'Tax Rate',
      'pvp_rules': 'PvP Rules',
      'trade_policy': 'Trade Policy',
      'immigration': 'Immigration',
      'defense': 'Defense Policy',
      'cultural': 'Cultural Policy'
    };
    return labels[type] || type.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase());
  };

  const getGovernanceTypeLabel = (type: string) => {
    const labels: Record<string, string> = {
      'autocracy': 'Autocracy',
      'democracy': 'Democracy',
      'council': 'Council Republic'
    };
    return labels[type] || type;
  };

  const handleRegionSelect = (selectedRegion: Region) => {
    setRegion(selectedRegion);
    setEconomicConfig({
      tax_rate: selectedRegion.tax_rate || 0.10,
      starting_credits: selectedRegion.starting_credits || 1000,
      trade_bonuses: selectedRegion.trade_bonuses || {},
      economic_specialization: selectedRegion.economic_specialization || ''
    });
    setGovernanceConfig({
      governance_type: selectedRegion.governance_type || 'autocracy',
      voting_threshold: selectedRegion.voting_threshold || 0.51,
      election_frequency_days: 90,
      constitutional_text: selectedRegion.constitutional_text || '',
      governance_quorum_pct: selectedRegion.governance_quorum_pct ?? 0.33
    });
  };

  if (!region) {
    return (
      <div className="regional-governor-dashboard">
        <div className="loading-message">
          {loading ? 'Loading regional data...' : 'No region found. You need to own a region to access this dashboard.'}
        </div>
      </div>
    );
  }

  return (
    <div className="regional-governor-dashboard">
      <div className="governor-header">
        <h1>Regional Governor Dashboard</h1>
        <div className="region-info">
          <h2>{region.display_name}</h2>
          <p>Governance Type: {getGovernanceTypeLabel(region.governance_type)} | Status: {region.status}</p>
          {isAdmin && allRegions.length > 1 && (
            <div style={{ marginTop: '8px' }}>
              <label style={{ marginRight: '8px', color: '#9ca3af' }}>View Region:</label>
              <select
                value={region.id}
                onChange={(e) => {
                  const selected = allRegions.find(r => r.id === e.target.value);
                  if (selected) handleRegionSelect(selected);
                }}
                style={{ padding: '4px 8px', borderRadius: '4px', background: '#1f2937', color: '#e5e7eb', border: '1px solid #374151' }}
              >
                {allRegions.map(r => (
                  <option key={r.id} value={r.id}>{r.display_name || r.name}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      </div>

      {error && (
        <div className="error-message">
          {error}
          <button onClick={() => setError(null)} className="error-close">×</button>
        </div>
      )}

      {success && (
        <div className="success-message">
          {success}
          <button onClick={() => setSuccess(null)} className="error-close">×</button>
        </div>
      )}

      <div className="governor-tabs">
        {(['overview', 'governance', 'economy', 'policies', 'elections', 'members', 'diplomacy', 'culture'] as const).map(tab => {
          const tabLabel: Record<typeof tab, string> = {
            overview: 'Overview',
            governance: 'Governance',
            economy: 'Economy',
            policies: 'Policies',
            elections: 'Elections',
            members: 'Members',
            diplomacy: 'Diplomacy — limited',
            culture: 'Culture — read-only',
          };
          return (
          <button
            key={tab}
            className={`tab-button ${activeTab === tab ? 'active' : ''}`}
            onClick={() => setActiveTab(tab)}
          >
            {tabLabel[tab]}
          </button>
          );
        })}
      </div>

      <div className="governor-content">
        {activeTab === 'overview' && (
          <div className="overview-tab">
            {/* Quick Actions */}
            <div className="quick-actions">
              <h3>Quick Actions</h3>
              <div className="action-buttons">
                <button
                  onClick={() => setActiveTab('policies')}
                  className="action-button primary"
                >
                  Create Policy
                </button>
                <button
                  onClick={() => setActiveTab('elections')}
                  className="action-button secondary"
                >
                  Manage Elections
                </button>
                <button
                  onClick={loadRegionalData}
                  className="action-button refresh"
                  disabled={loading}
                >
                  Refresh Data
                </button>
              </div>
            </div>
            {/* Regional Overview */}
            <div className="overview-grid">
              <div className="stat-card">
                <h4>Total Population</h4>
                <div className="stat-value">{formatNumber(stats?.total_population)}</div>
                <div className="stat-breakdown">
                  <div>Citizens: {formatNumber(stats?.citizen_count)}</div>
                  <div>Residents: {formatNumber(stats?.resident_count)}</div>
                  <div>Visitors: {formatNumber(stats?.visitor_count)}</div>
                </div>
              </div>

              <div className="stat-card">
                <h4>Territory</h4>
                <div className="stat-value">{formatNumber(region.total_sectors)}</div>
                <div className="stat-label">Sectors</div>
                <div className="stat-breakdown">
                  <div>Planets: {formatNumber(stats?.planets_count)}</div>
                  <div>Ports: {formatNumber(stats?.stations_count)}</div>
                </div>
              </div>

              <div className="stat-card">
                <h4>Economy</h4>
                <div className="stat-value">{formatCurrency(stats?.total_revenue)}</div>
                <div className="stat-label">Total Revenue</div>
                <div className="stat-breakdown">
                  <div>Trade Volume (30d): {formatCurrency(stats?.trade_volume_30d)}</div>
                  <div>Tax Rate: {formatPercentage(region.tax_rate)}</div>
                </div>
              </div>

              <div className="stat-card">
                <h4>Governance</h4>
                <div className="stat-value">{formatNumber(stats?.active_elections)}</div>
                <div className="stat-label">Active Elections</div>
                <div className="stat-breakdown">
                  <div>Pending Policies: {formatNumber(stats?.pending_policies)}</div>
                  <div>Treaties: {formatNumber(stats?.treaties_count)}</div>
                </div>
              </div>

              <div className="stat-card">
                <h4>Military</h4>
                <div className="stat-value">{formatNumber(stats?.ships_count)}</div>
                <div className="stat-label">Total Ships</div>
                <div className="stat-breakdown">
                  <div>Avg. Reputation: {stats?.average_reputation != null ? stats.average_reputation.toFixed(1) : '—'}</div>
                </div>
              </div>

              <div className="stat-card">
                <h4>Activity</h4>
                <div className="stat-value">{formatNumber(region.active_players_30d)}</div>
                <div className="stat-label">Active Players (30d)</div>
                <div className="stat-breakdown">
                  <div>Specialization: {region.economic_specialization || 'None'}</div>
                </div>
              </div>
            </div>


          </div>
        )}

        {activeTab === 'governance' && (
          <div className="governance-tab">
            <h3>Governance Configuration</h3>
            
            <div className="config-form">
              <div className="form-group">
                <label>Governance Type</label>
                <select
                  value={governanceConfig.governance_type}
                  onChange={(e) => setGovernanceConfig(prev => ({...prev, governance_type: e.target.value}))}
                >
                  <option value="autocracy">Autocracy</option>
                  <option value="democracy">Democracy</option>
                  <option value="council">Council Republic</option>
                </select>
                <small>Determines how decisions are made in your region</small>
              </div>

              <div className="form-group">
                <label>Voting Threshold</label>
                <input
                  type="number"
                  min="0.1"
                  max="0.9"
                  step="0.01"
                  value={governanceConfig.voting_threshold}
                  onChange={(e) => setGovernanceConfig(prev => ({...prev, voting_threshold: parseFloat(e.target.value)}))}
                />
                <small>Percentage of votes required to pass policies ({formatPercentage(governanceConfig.voting_threshold)})</small>
              </div>

              <div className="form-group">
                <label>Quorum Participation Threshold</label>
                <input
                  type="number"
                  min="0.25"
                  max="0.60"
                  step="0.01"
                  value={governanceConfig.governance_quorum_pct}
                  onChange={(e) => setGovernanceConfig(prev => ({...prev, governance_quorum_pct: parseFloat(e.target.value)}))}
                />
                <small>Share of eligible voters required for a vote to count ({formatPercentage(governanceConfig.governance_quorum_pct)}, must stay between 25% and 60%)</small>
              </div>

              <div className="form-group">
                <label>Election Frequency (days)</label>
                <input
                  type="number"
                  min="30"
                  max="365"
                  value={governanceConfig.election_frequency_days}
                  onChange={(e) => setGovernanceConfig(prev => ({...prev, election_frequency_days: parseInt(e.target.value)}))}
                />
                <small>How often elections are held for regional positions</small>
              </div>

              <div className="form-group">
                <label>Constitutional Text</label>
                <textarea
                  value={governanceConfig.constitutional_text}
                  onChange={(e) => setGovernanceConfig(prev => ({...prev, constitutional_text: e.target.value}))}
                  placeholder="Define the fundamental laws and principles of your region..."
                  rows={6}
                />
                <small>The fundamental laws that govern your region</small>
              </div>

              <button
                onClick={updateGovernanceConfig}
                className="action-button primary"
                disabled={loading}
              >
                {loading ? 'Updating...' : 'Update Governance'}
              </button>
            </div>
          </div>
        )}

        {activeTab === 'economy' && (
          <div className="economy-tab">
            <h3>Economic Configuration</h3>
            
            <div className="config-form">
              <div className="form-group">
                <label>Tax Rate</label>
                <input
                  type="number"
                  min="0.05"
                  max="0.25"
                  step="0.01"
                  value={economicConfig.tax_rate}
                  onChange={(e) => setEconomicConfig(prev => ({...prev, tax_rate: parseFloat(e.target.value)}))}
                />
                <small>Tax rate applied to economic activities ({formatPercentage(economicConfig.tax_rate)})</small>
              </div>

              <div className="form-group">
                <label>Starting Credits</label>
                <input
                  type="number"
                  min="100"
                  max="10000"
                  value={economicConfig.starting_credits}
                  onChange={(e) => setEconomicConfig(prev => ({...prev, starting_credits: parseInt(e.target.value)}))}
                />
                <small>Credits given to new players when they join your region</small>
              </div>

              <div className="form-group">
                <label>Economic Specialization</label>
                <select
                  value={economicConfig.economic_specialization}
                  onChange={(e) => setEconomicConfig(prev => ({...prev, economic_specialization: e.target.value}))}
                >
                  <option value="">None</option>
                  <option value="mining">Mining</option>
                  <option value="manufacturing">Manufacturing</option>
                  <option value="agriculture">Agriculture</option>
                  <option value="trade">Trade Hub</option>
                  <option value="research">Research & Development</option>
                  <option value="tourism">Tourism</option>
                  <option value="military">Military Industrial</option>
                </select>
                <small>Specialization provides bonuses to specific economic activities</small>
              </div>

              <div className="form-group">
                <label>Trade Bonuses</label>
                <div className="trade-bonuses">
                  {['ore', 'food', 'technology', 'luxury', 'energy'].map(resource => (
                    <div key={resource} className="bonus-input">
                      <label>{resource.charAt(0).toUpperCase() + resource.slice(1)}</label>
                      <input
                        type="number"
                        min="1.0"
                        max="3.0"
                        step="0.1"
                        value={economicConfig.trade_bonuses[resource] || 1.0}
                        onChange={(e) => setEconomicConfig(prev => ({
                          ...prev, 
                          trade_bonuses: {
                            ...prev.trade_bonuses,
                            [resource]: parseFloat(e.target.value)
                          }
                        }))}
                      />
                    </div>
                  ))}
                </div>
                <small>Multipliers for trade in different resource types</small>
              </div>

              <button
                onClick={updateEconomicConfig}
                className="action-button primary"
                disabled={loading}
              >
                {loading ? 'Updating...' : 'Update Economy'}
              </button>
            </div>
          </div>
        )}

        {activeTab === 'policies' && (
          <div className="policies-tab">
            <div className="policies-header">
              <h3>Regional Policies</h3>
              <button
                onClick={() => setShowPolicyForm(true)}
                className="action-button primary"
              >
                Create Policy
              </button>
            </div>

            {showPolicyForm && (
              <div className="policy-form">
                <h4>Create New Policy</h4>
                <div className="form-group">
                  <label>Policy Type</label>
                  <select
                    value={newPolicy.policy_type}
                    onChange={(e) => setNewPolicy(prev => ({...prev, policy_type: e.target.value}))}
                  >
                    <option value="tax_rate">Tax Rate</option>
                    <option value="pvp_rules">PvP Rules</option>
                    <option value="trade_policy">Trade Policy</option>
                    <option value="immigration">Immigration</option>
                    <option value="defense">Defense Policy</option>
                    <option value="cultural">Cultural Policy</option>
                  </select>
                </div>

                <div className="form-group">
                  <label>Title</label>
                  <input
                    type="text"
                    value={newPolicy.title}
                    onChange={(e) => setNewPolicy(prev => ({...prev, title: e.target.value}))}
                    placeholder="Policy title..."
                  />
                </div>

                <div className="form-group">
                  <label>Description</label>
                  <textarea
                    value={newPolicy.description}
                    onChange={(e) => setNewPolicy(prev => ({...prev, description: e.target.value}))}
                    placeholder="Detailed description of the policy..."
                    rows={4}
                  />
                </div>

                <div className="form-actions">
                  <button onClick={createPolicy} className="action-button primary" disabled={loading}>
                    {loading ? 'Creating...' : 'Create Policy'}
                  </button>
                  <button onClick={() => setShowPolicyForm(false)} className="action-button secondary">
                    Cancel
                  </button>
                </div>
              </div>
            )}

            <div className="policies-list">
              {policies.length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Votes For</th>
                      <th>Votes Against</th>
                      <th>Approval</th>
                      <th>Closes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {policies.map(policy => (
                      <tr key={policy.id}>
                        <td>{policy.title}</td>
                        <td>{getPolicyTypeLabel(policy.policy_type)}</td>
                        <td>
                          <span className={`status-badge ${getStatusColor(policy.status)}`}>
                            {policy.status}
                          </span>
                        </td>
                        <td>{formatNumber(policy.votes_for)}</td>
                        <td>{formatNumber(policy.votes_against)}</td>
                        <td>{policy.approval_percentage.toFixed(1)}%</td>
                        <td>{new Date(policy.voting_closes_at).toLocaleDateString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="no-data">No policies found</div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'elections' && (
          <div className="elections-tab">
            <div className="elections-header">
              <h3>Regional Elections</h3>
              <div className="election-actions">
                <button
                  onClick={() => startElection('governor')}
                  className="action-button primary"
                  disabled={loading}
                >
                  Start Governor Election
                </button>
                <button
                  onClick={() => startElection('council_member')}
                  className="action-button secondary"
                  disabled={loading}
                >
                  Start Council Election
                </button>
              </div>
            </div>

            <div className="elections-list">
              {elections.length > 0 ? (
                <div className="elections-grid">
                  {elections.map(election => (
                    <div key={election.id} className="election-card">
                      <h4>{election.position.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}</h4>
                      <div className="election-status">
                        <span className={`status-badge ${getStatusColor(election.status)}`}>
                          {election.status}
                        </span>
                      </div>
                      
                      <div className="election-period">
                        <div>Opens: {new Date(election.voting_opens_at).toLocaleDateString()}</div>
                        <div>Closes: {new Date(election.voting_closes_at).toLocaleDateString()}</div>
                      </div>

                      <div className="candidates">
                        <h5>Candidates ({election.candidates.length})</h5>
                        {election.candidates.map(candidate => (
                          <div key={candidate.player_id} className="candidate">
                            <span>{candidate.player_name}</span>
                            {candidate.vote_count !== undefined && (
                              <span className="vote-count">{formatNumber(candidate.vote_count)} votes</span>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="no-data">No elections found</div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'members' && (
          <div className="members-tab">
            <h3>Regional Members</h3>
            <div className="form-group">
              <small>
                Voting power ranges 0.0–5.0 (citizen tier target {CITIZEN_DEFAULT_VOTING_POWER.toFixed(1)});
                setting a member to 0.0 revokes their voting rights. Local rank is a free-text title (max 50 characters).
              </small>
            </div>

            <div className="members-list">
              {members.length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>Username</th>
                      <th>Type</th>
                      <th>Reputation</th>
                      <th>Local Rank</th>
                      <th>Voting Power</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map(member => (
                      <tr key={member.player_id}>
                        <td>{member.username}</td>
                        <td>{member.membership_type}</td>
                        <td>{formatNumber(member.reputation_score)}</td>
                        <td>
                          <input
                            type="text"
                            maxLength={50}
                            value={member.local_rank || ''}
                            onChange={(e) => handleMemberFieldChange(member.player_id, 'local_rank', e.target.value)}
                            style={{ width: '140px', padding: '6px 8px' }}
                          />
                        </td>
                        <td>
                          <input
                            type="number"
                            min="0.0"
                            max="5.0"
                            step="0.1"
                            value={member.voting_power}
                            onChange={(e) => handleMemberFieldChange(member.player_id, 'voting_power', parseFloat(e.target.value))}
                            style={{ width: '80px', padding: '6px 8px' }}
                          />
                        </td>
                        <td>
                          <div style={{ display: 'flex', gap: '8px' }}>
                            <button
                              onClick={() => handleMemberFieldChange(member.player_id, 'voting_power', CITIZEN_DEFAULT_VOTING_POWER)}
                              className="action-button small secondary"
                              disabled={loading}
                              title={`Set voting power to the citizen default (${CITIZEN_DEFAULT_VOTING_POWER})`}
                            >
                              Citizen Default
                            </button>
                            <button
                              onClick={() => updateMemberDials(member.player_id)}
                              className="action-button small primary"
                              disabled={loading}
                            >
                              Save
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="no-data">No members found</div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'diplomacy' && (
          <div className="diplomacy-tab">
            <h3>Diplomatic Relations — limited</h3>
            
            <div className="treaties-list">
              <h4>Active Treaties</h4>
              {treaties.length > 0 ? (
                <table>
                  <thead>
                    <tr>
                      <th>Partner Region</th>
                      <th>Treaty Type</th>
                      <th>Signed</th>
                      <th>Expires</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {treaties.map(treaty => (
                      <tr key={treaty.id}>
                        <td>
                          {treaty.region_a_name === region.name ? treaty.region_b_name : treaty.region_a_name}
                        </td>
                        <td>{treaty.treaty_type.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}</td>
                        <td>{new Date(treaty.signed_at).toLocaleDateString()}</td>
                        <td>{treaty.expires_at ? new Date(treaty.expires_at).toLocaleDateString() : 'Permanent'}</td>
                        <td>
                          <span className={`status-badge ${getStatusColor(treaty.status)}`}>
                            {treaty.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="no-data">No treaties found</div>
              )}
            </div>

            <div
              role="note"
              style={{
                margin: '16px 0 0 0',
                padding: '10px 12px',
                background: 'rgba(234, 179, 8, 0.12)',
                border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px',
                color: '#fbbf24',
                fontSize: '0.82rem',
                lineHeight: 1.4,
              }}
            >
              Diplomatic actions (trade agreements, defense pacts, cultural exchange,
              diplomatic messages) are unavailable — no regional diplomacy endpoint.
              Existing treaties are listed above when present. This tab does not invent
              an Actions button bar.
            </div>
          </div>
        )}

        {activeTab === 'culture' && (
          <div className="culture-tab">
            <h3>Cultural Identity — read-only</h3>

            <div
              role="note"
              style={{
                margin: '0 0 12px 0',
                padding: '10px 12px',
                background: 'rgba(234, 179, 8, 0.12)',
                border: '1px solid rgba(234, 179, 8, 0.35)',
                borderRadius: '6px',
                color: '#fbbf24',
                fontSize: '0.82rem',
                lineHeight: 1.4,
              }}
            >
              Regional cultural identity (theme, motto, traditions, language) is set by a
              region&apos;s <strong>owner</strong> from their region console. There is no
              admin endpoint to edit it for a selected region — this tab does not invent
              edit controls. Current values, when present, are shown below.
            </div>
              {region?.aesthetic_theme?.variant || region?.language_pack?.variant ? (
                <div className="culture-current">
                  <div className="stat-breakdown">
                    <div>Theme: {region?.aesthetic_theme?.variant || '—'}</div>
                    <div>Language: {region?.language_pack?.variant || '—'}</div>
                  </div>
                </div>
              ) : null}
          </div>
        )}
      </div>
    </div>
  );
};

export default RegionalGovernorDashboard;