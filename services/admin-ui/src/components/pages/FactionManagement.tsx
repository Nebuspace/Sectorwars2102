import React, { useState, useEffect, useCallback, useMemo } from 'react';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { useToast, useConfirm } from '../../contexts/ToastContext';
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

// Mirrors models/faction.py FactionType exactly (Pydantic validates against
// these literal enum values, not the humanized labels formatType() renders).
const FACTION_TYPES = [
  'Federation', 'Independents', 'Pirates', 'Merchants', 'Explorers',
  'Military', 'Mining', 'Outlaws', 'Syndicate', 'Concord',
];

// models/faction.py:95 documents the 3-value scale (hostile, neutral, friendly);
// diplomacy_stance itself is an unconstrained string column server-side.
const DIPLOMACY_STANCES = ['hostile', 'neutral', 'friendly'];

interface FactionFormState {
  name: string;
  faction_type: string;
  description: string;
  base_pricing_modifier: string;
  trade_specialties: string;
  aggression_level: string;
  diplomacy_stance: string;
  color_primary: string;
  color_secondary: string;
  logo_url: string;
}

const EMPTY_FACTION_FORM: FactionFormState = {
  name: '',
  faction_type: FACTION_TYPES[0],
  description: '',
  base_pricing_modifier: '1.0',
  trade_specialties: '',
  aggression_level: '5',
  diplomacy_stance: 'neutral',
  color_primary: '#3b82f6',
  color_secondary: '#1e3a8a',
  logo_url: '',
};

const factionToFormState = (faction: Faction): FactionFormState => ({
  name: faction.name,
  faction_type: faction.faction_type,
  description: faction.description ?? '',
  base_pricing_modifier: String(faction.base_pricing_modifier),
  trade_specialties: faction.trade_specialties.join(', '),
  aggression_level: String(faction.aggression_level),
  diplomacy_stance: faction.diplomacy_stance,
  color_primary: faction.color_primary ?? '#3b82f6',
  color_secondary: faction.color_secondary ?? '#1e3a8a',
  logo_url: faction.logo_url ?? '',
});

// Shared by create (POST) and edit (PUT) — both accept the same field set.
const buildFactionPayload = (form: FactionFormState) => ({
  name: form.name.trim(),
  faction_type: form.faction_type,
  description: form.description.trim() || null,
  base_pricing_modifier: parseFloat(form.base_pricing_modifier),
  trade_specialties: form.trade_specialties
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
  aggression_level: parseInt(form.aggression_level, 10),
  diplomacy_stance: form.diplomacy_stance,
  color_primary: form.color_primary || null,
  color_secondary: form.color_secondary || null,
  logo_url: form.logo_url.trim() || null,
});

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
  const toast = useToast();
  const confirm = useConfirm();

  const [factions, setFactions] = useState<Faction[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const [searchTerm, setSearchTerm] = useState<string>('');
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [stanceFilter, setStanceFilter] = useState<string>('all');

  // Create faction
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createForm, setCreateForm] = useState<FactionFormState>(EMPTY_FACTION_FORM);
  const [creating, setCreating] = useState(false);

  // Edit faction (name/type/description/pricing/specialties/aggression/stance/colors/logo)
  const [editingFaction, setEditingFaction] = useState<Faction | null>(null);
  const [editForm, setEditForm] = useState<FactionFormState>(EMPTY_FACTION_FORM);
  const [saving, setSaving] = useState(false);

  // Territory control
  const [territoryFaction, setTerritoryFaction] = useState<Faction | null>(null);
  const [territoryInput, setTerritoryInput] = useState('');
  const [homeSectorInput, setHomeSectorInput] = useState('');
  const [savingTerritory, setSavingTerritory] = useState(false);

  // Player reputation adjustment
  const [reputationFaction, setReputationFaction] = useState<Faction | null>(null);
  const [reputationForm, setReputationForm] = useState({
    playerId: '',
    change: '10',
    reason: 'Admin adjustment',
  });
  const [savingReputation, setSavingReputation] = useState(false);

  const anyMutationInFlight = creating || saving || savingTerritory || savingReputation;

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await api.get<Faction[]>('/api/v1/admin/factions/');
      setFactions(response.data ?? []);
    } catch (err) {
      console.error('Error fetching factions:', err);
      setError('Failed to load factions.');
      setFactions([]);
    }

    setLoading(false);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const openCreateModal = () => {
    setCreateForm(EMPTY_FACTION_FORM);
    setShowCreateModal(true);
  };

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = createForm.name.trim();
    if (!name) return;

    const ok = await confirm({
      title: 'Create Faction',
      message: `Create faction "${name}"?`,
      confirmLabel: 'Create',
    });
    if (!ok) return;

    setCreating(true);
    try {
      await api.post('/api/v1/admin/factions/', buildFactionPayload(createForm));
      toast.success(`Faction "${name}" created.`);
      setShowCreateModal(false);
      setCreateForm(EMPTY_FACTION_FORM);
      await fetchData();
    } catch (err: any) {
      console.error('Error creating faction:', err);
      toast.error(err.response?.data?.detail || 'Failed to create faction.');
    } finally {
      setCreating(false);
    }
  };

  const openEditModal = (faction: Faction) => {
    setEditingFaction(faction);
    setEditForm(factionToFormState(faction));
  };

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingFaction) return;

    const ok = await confirm({
      title: 'Update Faction',
      message: `Save changes to "${editingFaction.name}"?`,
      confirmLabel: 'Save',
    });
    if (!ok) return;

    setSaving(true);
    try {
      await api.put(`/api/v1/admin/factions/${editingFaction.id}`, buildFactionPayload(editForm));
      toast.success(`Faction "${editForm.name.trim()}" updated.`);
      setEditingFaction(null);
      await fetchData();
    } catch (err: any) {
      console.error('Error updating faction:', err);
      toast.error(err.response?.data?.detail || 'Failed to update faction.');
    } finally {
      setSaving(false);
    }
  };

  const openTerritoryModal = (faction: Faction) => {
    setTerritoryFaction(faction);
    setTerritoryInput(faction.territory_sectors.join('\n'));
    setHomeSectorInput(faction.home_sector_id ?? '');
  };

  const handleTerritorySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!territoryFaction) return;

    const sectorIds = territoryInput
      .split(/[\n,]/)
      .map((s) => s.trim())
      .filter(Boolean);

    const ok = await confirm({
      title: 'Update Territory',
      message: `Set ${sectorIds.length} controlled sector${sectorIds.length === 1 ? '' : 's'} for "${territoryFaction.name}"?`,
      confirmLabel: 'Save',
    });
    if (!ok) return;

    setSavingTerritory(true);
    try {
      await api.put(`/api/v1/admin/factions/${territoryFaction.id}/territory`, {
        sector_ids: sectorIds,
        home_sector_id: homeSectorInput.trim() || null,
      });
      toast.success(`Territory updated for "${territoryFaction.name}".`);
      setTerritoryFaction(null);
      await fetchData();
    } catch (err: any) {
      console.error('Error updating faction territory:', err);
      toast.error(
        err.response?.data?.detail || 'Failed to update territory. Check that sector IDs are valid.'
      );
    } finally {
      setSavingTerritory(false);
    }
  };

  const openReputationModal = (faction: Faction) => {
    setReputationFaction(faction);
    setReputationForm({ playerId: '', change: '10', reason: 'Admin adjustment' });
  };

  const handleReputationSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reputationFaction) return;

    const playerId = reputationForm.playerId.trim();
    const change = parseInt(reputationForm.change, 10);
    if (!playerId || Number.isNaN(change)) return;

    const ok = await confirm({
      title: 'Adjust Reputation',
      message: `Apply a ${change >= 0 ? '+' : ''}${change} reputation change with "${reputationFaction.name}" for this player?`,
      confirmLabel: 'Apply',
    });
    if (!ok) return;

    setSavingReputation(true);
    try {
      const response = await api.put(`/api/v1/admin/factions/${reputationFaction.id}/reputation`, {
        player_id: playerId,
        change,
        reason: reputationForm.reason.trim() || 'Admin adjustment',
      });
      const data = response.data as { new_value?: number; new_level?: string; new_title?: string };
      toast.success(
        `Reputation updated: ${data.new_value ?? '?'} (${data.new_level ?? 'unknown'}${
          data.new_title ? ` — ${data.new_title}` : ''
        }).`
      );
      setReputationFaction(null);
    } catch (err: any) {
      console.error('Error adjusting faction reputation:', err);
      toast.error(
        err.response?.data?.detail || 'Failed to adjust reputation. Check the player ID.'
      );
    } finally {
      setSavingReputation(false);
    }
  };

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
    };
  }, [factions]);

  if (loading) {
    return (
      <div className="faction-management">
        <PageHeader
          title="Faction Management"
          subtitle="Monitor factions, territory, and diplomacy"
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
        <button type="button" className="faction-btn" onClick={fetchData} disabled={loading}>
          Refresh
        </button>
        <button type="button" className="faction-btn faction-btn-primary" onClick={openCreateModal}>
          + Create Faction
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
                  <th>Actions</th>
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
                    <td>
                      <div className="faction-actions">
                        <button
                          type="button"
                          className="faction-btn faction-btn-small"
                          onClick={() => openEditModal(faction)}
                          disabled={anyMutationInFlight}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="faction-btn faction-btn-small"
                          onClick={() => openTerritoryModal(faction)}
                          disabled={anyMutationInFlight}
                        >
                          Territory
                        </button>
                        <button
                          type="button"
                          className="faction-btn faction-btn-small"
                          onClick={() => openReputationModal(faction)}
                          disabled={anyMutationInFlight}
                        >
                          Reputation
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create faction modal */}
      {showCreateModal && (
        <div className="modal-overlay" onClick={() => !creating && setShowCreateModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Create Faction</h3>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setShowCreateModal(false)}
                disabled={creating}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <FactionFormFields
                form={createForm}
                onChange={setCreateForm}
                onSubmit={handleCreateSubmit}
                submitLabel="Create Faction"
                busy={creating}
                onCancel={() => setShowCreateModal(false)}
              />
            </div>
          </div>
        </div>
      )}

      {/* Edit faction modal */}
      {editingFaction && (
        <div className="modal-overlay" onClick={() => !saving && setEditingFaction(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Edit Faction: {editingFaction.name}</h3>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setEditingFaction(null)}
                disabled={saving}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <FactionFormFields
                form={editForm}
                onChange={setEditForm}
                onSubmit={handleEditSubmit}
                submitLabel="Save Changes"
                busy={saving}
                onCancel={() => setEditingFaction(null)}
              />
            </div>
          </div>
        </div>
      )}

      {/* Territory control modal */}
      {territoryFaction && (
        <div className="modal-overlay" onClick={() => !savingTerritory && setTerritoryFaction(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Territory: {territoryFaction.name}</h3>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setTerritoryFaction(null)}
                disabled={savingTerritory}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleTerritorySubmit}>
                <div className="form-group">
                  <label className="form-label">Controlled Sector IDs</label>
                  <textarea
                    className="form-textarea"
                    value={territoryInput}
                    onChange={(e) => setTerritoryInput(e.target.value)}
                    placeholder={'One sector UUID per line or comma-separated, e.g.\n3fa85f64-5717-4562-b3fc-2c963f66afa6'}
                    rows={6}
                    disabled={savingTerritory}
                  />
                  <p className="form-help">
                    Replaces the faction&apos;s full controlled-sector list. No sector picker is
                    wired yet — paste sector UUIDs directly (see NO-CANON note in DECISIONS for a
                    future map-based picker).
                  </p>
                </div>
                <div className="form-group">
                  <label className="form-label">Home Sector ID (optional)</label>
                  <input
                    type="text"
                    className="form-input"
                    value={homeSectorInput}
                    onChange={(e) => setHomeSectorInput(e.target.value)}
                    placeholder="Sector UUID"
                    disabled={savingTerritory}
                  />
                </div>
                <div className="modal-footer">
                  <button
                    type="button"
                    className="btn btn-outline"
                    onClick={() => setTerritoryFaction(null)}
                    disabled={savingTerritory}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary" disabled={savingTerritory}>
                    {savingTerritory ? 'Saving…' : 'Save Territory'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {/* Player reputation adjustment modal */}
      {reputationFaction && (
        <div className="modal-overlay" onClick={() => !savingReputation && setReputationFaction(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3 className="modal-title">Adjust Reputation: {reputationFaction.name}</h3>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                onClick={() => setReputationFaction(null)}
                disabled={savingReputation}
              >
                ×
              </button>
            </div>
            <div className="modal-body">
              <form onSubmit={handleReputationSubmit}>
                <div className="form-group">
                  <label className="form-label">Player ID</label>
                  <input
                    type="text"
                    className="form-input"
                    value={reputationForm.playerId}
                    onChange={(e) =>
                      setReputationForm({ ...reputationForm, playerId: e.target.value })
                    }
                    placeholder="Player UUID"
                    required
                    disabled={savingReputation}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Change</label>
                  <input
                    type="number"
                    className="form-input"
                    min={-100}
                    max={100}
                    step={1}
                    value={reputationForm.change}
                    onChange={(e) =>
                      setReputationForm({ ...reputationForm, change: e.target.value })
                    }
                    required
                    disabled={savingReputation}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Reason</label>
                  <input
                    type="text"
                    className="form-input"
                    value={reputationForm.reason}
                    onChange={(e) =>
                      setReputationForm({ ...reputationForm, reason: e.target.value })
                    }
                    disabled={savingReputation}
                  />
                </div>
                <div className="modal-footer">
                  <button
                    type="button"
                    className="btn btn-outline"
                    onClick={() => setReputationFaction(null)}
                    disabled={savingReputation}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary" disabled={savingReputation}>
                    {savingReputation ? 'Applying…' : 'Apply Change'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

// Shared field set for the create + edit modals (FactionCreateRequest /
// FactionUpdateRequest accept the same shape server-side).
interface FactionFormFieldsProps {
  form: FactionFormState;
  onChange: (form: FactionFormState) => void;
  onSubmit: (e: React.FormEvent) => void;
  submitLabel: string;
  busy: boolean;
  onCancel: () => void;
}

const FactionFormFields: React.FC<FactionFormFieldsProps> = ({
  form,
  onChange,
  onSubmit,
  submitLabel,
  busy,
  onCancel,
}) => (
  <form onSubmit={onSubmit}>
    <div className="form-group">
      <label className="form-label">Name</label>
      <input
        type="text"
        className="form-input"
        value={form.name}
        onChange={(e) => onChange({ ...form, name: e.target.value })}
        required
        disabled={busy}
      />
    </div>
    <div className="form-group">
      <label className="form-label">Faction Type</label>
      <select
        className="form-select"
        value={form.faction_type}
        onChange={(e) => onChange({ ...form, faction_type: e.target.value })}
        disabled={busy}
      >
        {FACTION_TYPES.map((t) => (
          <option key={t} value={t}>
            {formatType(t)}
          </option>
        ))}
      </select>
    </div>
    <div className="form-group">
      <label className="form-label">Description</label>
      <textarea
        className="form-textarea"
        value={form.description}
        onChange={(e) => onChange({ ...form, description: e.target.value })}
        rows={3}
        disabled={busy}
      />
    </div>
    <div className="form-group">
      <label className="form-label">Diplomacy Stance</label>
      <select
        className="form-select"
        value={form.diplomacy_stance}
        onChange={(e) => onChange({ ...form, diplomacy_stance: e.target.value })}
        disabled={busy}
      >
        {DIPLOMACY_STANCES.map((s) => (
          <option key={s} value={s}>
            {formatType(s)}
          </option>
        ))}
      </select>
    </div>
    <div className="form-group">
      <label className="form-label">Aggression Level (1-10)</label>
      <input
        type="number"
        className="form-input"
        min={1}
        max={10}
        step={1}
        value={form.aggression_level}
        onChange={(e) => onChange({ ...form, aggression_level: e.target.value })}
        disabled={busy}
      />
    </div>
    <div className="form-group">
      <label className="form-label">Base Pricing Modifier (0.5-2.0x)</label>
      <input
        type="number"
        className="form-input"
        min={0.5}
        max={2}
        step={0.05}
        value={form.base_pricing_modifier}
        onChange={(e) => onChange({ ...form, base_pricing_modifier: e.target.value })}
        disabled={busy}
      />
    </div>
    <div className="form-group">
      <label className="form-label">Trade Specialties (comma-separated)</label>
      <input
        type="text"
        className="form-input"
        value={form.trade_specialties}
        onChange={(e) => onChange({ ...form, trade_specialties: e.target.value })}
        placeholder="ore, tech, luxury_goods"
        disabled={busy}
      />
    </div>
    <div className="form-group faction-color-row">
      <div>
        <label className="form-label">Primary Color</label>
        <input
          type="color"
          className="form-input faction-color-input"
          value={form.color_primary}
          onChange={(e) => onChange({ ...form, color_primary: e.target.value })}
          disabled={busy}
        />
      </div>
      <div>
        <label className="form-label">Secondary Color</label>
        <input
          type="color"
          className="form-input faction-color-input"
          value={form.color_secondary}
          onChange={(e) => onChange({ ...form, color_secondary: e.target.value })}
          disabled={busy}
        />
      </div>
    </div>
    <div className="form-group">
      <label className="form-label">Logo URL (optional)</label>
      <input
        type="text"
        className="form-input"
        value={form.logo_url}
        onChange={(e) => onChange({ ...form, logo_url: e.target.value })}
        disabled={busy}
      />
    </div>
    <div className="modal-footer">
      <button type="button" className="btn btn-outline" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
      <button type="submit" className="btn btn-primary" disabled={busy}>
        {busy ? 'Saving…' : submitLabel}
      </button>
    </div>
  </form>
);

export default FactionManagement;
