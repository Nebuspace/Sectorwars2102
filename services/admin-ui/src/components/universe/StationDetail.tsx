import React, { useEffect, useState } from 'react';
import { api } from '../../utils/auth';
import { axiosResponseStatus, formatAdminApiError } from '../../utils/adminApiError';
import './universe-detail.css';

interface PortDetailProps {
  port: any;
  onBack: () => void;
  onUpdate?: (updatedPort: any) => void;
}

/** StationClass enum ints 0–11 (models/station.py). */
const STATION_CLASS_OPTIONS = Array.from({ length: 12 }, (_, i) => String(i));

const COMMODITY_PRICE_FIELDS: Record<string, string> = {
  ore_price: 'ore',
  organics_price: 'organics',
  equipment_price: 'equipment',
};

/** Build PATCH body keys GS update_port actually applies (hasattr / commodities / _quantity). */
export function buildPortPatchPayload(field: string, value: any, port: any): Record<string, unknown> {
  if (field === 'tax_rate') {
    const percent = typeof value === 'number' ? value : parseFloat(String(value));
    return { tax_rate: (Number.isFinite(percent) ? percent : 0) / 100 };
  }
  if (field === 'owner_id') {
    const raw = String(value ?? '').trim();
    return { owner_id: raw === '' ? null : raw };
  }
  if (field === 'station_class') {
    return { station_class: parseInt(String(value), 10) };
  }
  if (field === 'defense_fighters' || field === 'defense_drones') {
    const drones = typeof value === 'number' ? value : parseInt(String(value), 10) || 0;
    return {
      defenses: {
        defense_drones: drones,
        max_defense_drones: port.max_defense_drones ?? 50,
        shield_strength: port.shields ?? port.port_shields ?? 50,
        patrol_ships: port.patrol_ships ?? 0,
      },
    };
  }
  const commodity = COMMODITY_PRICE_FIELDS[field];
  if (commodity) {
    const price = typeof value === 'number' ? value : parseFloat(String(value)) || 0;
    return { commodities: { [commodity]: { current_price: price } } };
  }
  return { [field]: value };
}

type PirateHoldingRow = {
  id: string;
  tier?: string | null;
  owner_player_id?: string | null;
};

function asIntegerSectorId(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n) || !Number.isInteger(n)) return null;
  return n;
}

/** Global integer sector_id from planet payload (LEG-4176 path shape — never a UUID). */
export function resolvePortAdminSectorId(port: unknown): number | null {
  if (port === null || typeof port !== 'object') return null;
  const p = port as { sector_id?: unknown; station?: { sector_id?: unknown } };
  return asIntegerSectorId(p.sector_id) ?? asIntegerSectorId(p.station?.sector_id);
}

function asPirateHoldings(data: unknown): PirateHoldingRow[] {
  const holdings = (data as { holdings?: unknown } | null)?.holdings;
  if (!Array.isArray(holdings)) return [];
  return holdings.filter(
    (row): row is PirateHoldingRow =>
      row !== null &&
      typeof row === 'object' &&
      typeof (row as PirateHoldingRow).id === 'string',
  );
}

function formatHoldingOwner(holding: PirateHoldingRow): string {
  const owner = holding.owner_player_id;
  if (owner === null || owner === undefined || String(owner).trim() === '') {
    return 'pirate-controlled';
  }
  return String(owner);
}

function formatHoldingTier(tier: unknown): string {
  if (tier === null || tier === undefined) return '—';
  const s = String(tier).trim();
  return s === '' ? '—' : s;
}

const PortDetail: React.FC<PortDetailProps> = ({ port, onBack, onUpdate }) => {
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [isLoading, setIsLoading] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [pirateHoldings, setPirateHoldings] = useState<PirateHoldingRow[]>([]);
  const [holdingsError, setHoldingsError] = useState<string | null>(null);
  const sectorId = resolvePortAdminSectorId(port);

  useEffect(() => {
    if (sectorId === null) {
      setPirateHoldings([]);
      setHoldingsError(null);
      return;
    }

    let cancelled = false;
    setHoldingsError(null);
    (async () => {
      try {
        const holdingsResponse = await api.get(
          `/api/v1/admin/sectors/${sectorId}/pirate-holdings`,
        );
        if (!cancelled) {
          setPirateHoldings(asPirateHoldings(holdingsResponse.data));
        }
      } catch (err: unknown) {
        if (cancelled) return;
        setPirateHoldings([]);
        if (axiosResponseStatus(err) !== 404) {
          setHoldingsError(
            formatAdminApiError(err, {
              fallback: 'Failed to load pirate holdings',
              scopeHint: 'admin.universe.manage',
            }),
          );
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sectorId]);

  const stationClass =
    port.station_class ?? port.port_class ?? 1;
  const defenseDrones =
    port.defense_drones ?? port.defense_fighters ?? 0;

  const handleEdit = (field: string, currentValue: any) => {
    setSaveError(null);
    setEditingField(field);
    setEditValues({ ...editValues, [field]: currentValue });
  };

  const handleSave = async (field: string) => {
    try {
      setIsLoading(true);
      setSaveError(null);
      const value = editValues[field];
      const payload = buildPortPatchPayload(field, value, port);

      await api.patch(`/api/v1/admin/ports/${port.id}`, payload);

      const updatedPort = { ...port, ...localPortUpdate(field, value, payload) };
      if (onUpdate) {
        onUpdate(updatedPort);
      }

      setEditingField(null);
    } catch (error) {
      setSaveError(
        formatAdminApiError(error, {
          fallback: `Failed to update ${field}`,
          scopeHint: 'admin.universe.manage',
        })
      );
    } finally {
      setIsLoading(false);
    }
  };

  const localPortUpdate = (field: string, value: any, payload: Record<string, unknown>) => {
    if (field === 'tax_rate') {
      return { tax_rate: (payload as { tax_rate: number }).tax_rate };
    }
    if (field === 'owner_id') {
      return { owner_id: (payload as { owner_id: string | null }).owner_id };
    }
    if (field === 'station_class') {
      return { station_class: (payload as { station_class: number }).station_class };
    }
    if (field === 'defense_fighters' || field === 'defense_drones') {
      const drones = (payload as { defenses: { defense_drones: number } }).defenses.defense_drones;
      return { defense_drones: drones, defense_fighters: drones };
    }
    const commodity = COMMODITY_PRICE_FIELDS[field];
    if (commodity) {
      return { [field]: value };
    }
    return { [field]: value };
  };

  const handleCancel = () => {
    setEditingField(null);
    setEditValues({});
  };

  const EditableField: React.FC<{
    field: string;
    value: any;
    type?: 'text' | 'number' | 'select';
    options?: string[];
  }> = ({ field, value, type = 'text', options }) => {
    const isEditing = editingField === field;
    
    if (isEditing) {
      return (
        <div className="editable-field editing">
          {type === 'select' && options ? (
            <select
              value={editValues[field] || value}
              onChange={(e) => setEditValues({ ...editValues, [field]: e.target.value })}
              disabled={isLoading}
            >
              {options.map(option => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          ) : (
            <input
              type={type}
              value={editValues[field] !== undefined ? editValues[field] : value}
              onChange={(e) => setEditValues({ 
                ...editValues, 
                [field]: type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value 
              })}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSave(field);
                if (e.key === 'Escape') handleCancel();
              }}
              disabled={isLoading}
              autoFocus
            />
          )}
          <div className="edit-actions">
            <button 
              onClick={() => handleSave(field)} 
              disabled={isLoading}
              className="save-btn"
            >
              ✓
            </button>
            <button 
              onClick={handleCancel} 
              disabled={isLoading}
              className="cancel-btn"
            >
              ✕
            </button>
          </div>
        </div>
      );
    }

    return (
      <span 
        className="editable-field clickable" 
        onClick={() => handleEdit(field, value)}
        title="Click to edit"
      >
        {value}
      </span>
    );
  };

  const getPortClassInfo = (portClass: number) => {
    const classInfo: { [key: number]: { name: string; description: string; color: string } } = {
      1: { name: 'Small Outpost', description: 'Basic trading post with minimal services', color: '#888' },
      2: { name: 'Standard Station', description: 'Common trading hub with standard services', color: '#668' },
      3: { name: 'Major Station', description: 'Large trading center with full services', color: '#486' },
      4: { name: 'Regional Hub', description: 'Advanced facility with premium services', color: '#468' },
      5: { name: 'Federation HQ', description: 'Elite trading center with all services', color: '#846' }
    };
    return classInfo[portClass] || classInfo[1];
  };

  const classInfo = getPortClassInfo(Number(stationClass));

  return (
    <div className="port-detail">
      <div className="detail-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Sector
        </button>
        <h2>🏪 {port.name}</h2>
        <div className="port-class" style={{ backgroundColor: classInfo.color }}>
          Class {stationClass}: {classInfo.name}
        </div>
      </div>

      <div className="detail-content">
        {saveError ? (
          <div className="admin-save-error" role="alert">
            {saveError}
          </div>
        ) : null}
        {holdingsError ? (
          <div className="admin-save-error" role="alert">
            {holdingsError}
          </div>
        ) : null}
        <div className="port-overview">
          <h3>Station Overview</h3>
          <div className="info-grid">
            <div className="info-item">
              <span className="label">Name:</span>
              <span className="value">
                <EditableField field="name" value={port.name} type="text" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Station Class:</span>
              <span className="value">
                <EditableField
                  field="station_class"
                  value={stationClass}
                  type="select"
                  options={STATION_CLASS_OPTIONS}
                />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Owner ID:</span>
              <span className="value">
                <EditableField
                  field="owner_id"
                  value={port.owner_id || ''}
                  type="text"
                />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Tax Rate:</span>
              <span className="value">
                <EditableField field="tax_rate" value={((port.tax_rate ?? 0) * 100).toFixed(1)} type="number" />%
              </span>
            </div>
            <div className="info-item">
              <span className="label">Defense Drones:</span>
              <span className="value">
                <EditableField field="defense_drones" value={defenseDrones} type="number" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Purchase Price:</span>
              <span className="value">{(Number(stationClass) * 250000).toLocaleString()} credits</span>
            </div>
          </div>
          <p className="port-description">{classInfo.description}</p>
        </div>

        <div className="commodities-section">
          <h3>Commodities Trading</h3>
          <div className="commodities-grid">
            <div className="commodity-card">
              <h4>⛏️ Ore</h4>
              <div className="commodity-info">
                <div className="quantity">
                  <span className="label">Quantity:</span>
                  <span className="value">
                    <EditableField field="ore_quantity" value={port.ore_quantity || 0} type="number" />
                  </span>
                </div>
                <div className="prices">
                  <div className="buy-price">
                    <span className="label">Buy:</span>
                    <span className="value">
                      <EditableField field="ore_price" value={port.ore_price || 25} type="number" /> cr
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div className="commodity-card">
              <h4>🌾 Organics</h4>
              <div className="commodity-info">
                <div className="quantity">
                  <span className="label">Quantity:</span>
                  <span className="value">
                    <EditableField field="organics_quantity" value={port.organics_quantity || 0} type="number" />
                  </span>
                </div>
                <div className="prices">
                  <div className="buy-price">
                    <span className="label">Buy:</span>
                    <span className="value">
                      <EditableField field="organics_price" value={port.organics_price || 15} type="number" /> cr
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div className="commodity-card">
              <h4>🔧 Equipment</h4>
              <div className="commodity-info">
                <div className="quantity">
                  <span className="label">Quantity:</span>
                  <span className="value">
                    <EditableField field="equipment_quantity" value={port.equipment_quantity || 0} type="number" />
                  </span>
                </div>
                <div className="prices">
                  <div className="buy-price">
                    <span className="label">Buy:</span>
                    <span className="value">
                      <EditableField field="equipment_price" value={port.equipment_price || 50} type="number" /> cr
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div className="commodity-card">
              <h4>⛽ Fuel</h4>
              <div className="commodity-info">
                <div className="quantity">
                  <span className="label">Quantity:</span>
                  <span className="value">
                    <EditableField field="fuel_quantity" value={port.fuel_quantity || 0} type="number" />
                  </span>
                </div>
                {port.fuel_capacity != null ? (
                  <div className="capacity read-only" title="Capacity is read-only">
                    Capacity: {port.fuel_capacity}
                  </div>
                ) : null}
              </div>
            </div>
            <div className="commodity-card">
              <h4>💎 Luxury Goods</h4>
              <div className="commodity-info">
                <div className="quantity">
                  <span className="label">Quantity:</span>
                  <span className="value">
                    <EditableField
                      field="luxury_goods_quantity"
                      value={port.luxury_goods_quantity || 0}
                      type="number"
                    />
                  </span>
                </div>
                {port.luxury_goods_capacity != null ? (
                  <div className="capacity read-only" title="Capacity is read-only">
                    Capacity: {port.luxury_goods_capacity}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>

        <div className="services-section">
          <h3>Station Services Port Services & Equipment Equipment</h3>
          <div className="services-grid">
            <div className="service-item">
              <span className="service-icon">🛡️</span>
              <span className="service-name">Station Shields</span>
              <span
                className="service-status"
                title="Not editable — update_port has no port_shields column"
              >
                {port.port_shields || 0} / 1000
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">🤖</span>
              <span className="service-name">Defense Drones</span>
              <span className="service-status">
                <EditableField
                  field="defense_drones"
                  value={defenseDrones}
                  type="number"
                />
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">🔧</span>
              <span className="service-name">Max Maintenance</span>
              <span
                className="service-status"
                title="Not editable — update_port has no max_maintenance column"
              >
                {port.max_maintenance || 100}%
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">💰</span>
              <span className="service-name">Buy Rate</span>
              <span
                className="service-status"
                title="Not editable — update_port has no buy_rate column"
              >
                {port.buy_rate || 95}%
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">💸</span>
              <span className="service-name">Sell Rate</span>
              <span
                className="service-status"
                title="Not editable — update_port has no sell_rate column"
              >
                {port.sell_rate || 105}%
              </span>
            </div>
          </div>
        </div>

        <div className="port-administration">
          <h3>Station Administration</h3>
          <div className="admin-actions">
            <div className="action-group">
              <h4>Economic Controls</h4>
              <button 
                className="admin-action-btn"
                onClick={() => {
                  const newQuantity = prompt('Enter new ore quantity:', port.ore_quantity?.toString() || '1000');
                  if (newQuantity) handleEdit('ore_quantity', parseInt(newQuantity));
                }}
              >
                📦 Adjust Ore Stock
              </button>
              <button 
                className="admin-action-btn"
                onClick={() => {
                  const newQuantity = prompt('Enter new organics quantity:', port.organics_quantity?.toString() || '1000');
                  if (newQuantity) handleEdit('organics_quantity', parseInt(newQuantity));
                }}
              >
                🌾 Adjust Organics Stock
              </button>
              <button 
                className="admin-action-btn"
                onClick={() => {
                  const newQuantity = prompt('Enter new equipment quantity:', port.equipment_quantity?.toString() || '1000');
                  if (newQuantity) handleEdit('equipment_quantity', parseInt(newQuantity));
                }}
              >
                🔧 Adjust Equipment Stock
              </button>
            </div>
            <div className="action-group">
              <h4>Security Controls</h4>
              <button 
                className="admin-action-btn"
                onClick={() => {
                  const newFighters = prompt(
                    'Enter new defense drone count:',
                    String(defenseDrones || 100)
                  );
                  if (newFighters) handleEdit('defense_drones', parseInt(newFighters, 10));
                }}
              >
                🤖 Deploy Defense Drones
              </button>
            </div>
          </div>
        </div>
        

        <div className="ships-panel" data-testid="pirate-holdings-panel">
          <h3>Pirate holdings</h3>
          {sectorId === null ? (
            <p data-testid="pirate-holdings-unavailable">
              Pirate holdings cannot be loaded: sector id is missing or not an integer.
            </p>
          ) : pirateHoldings.length === 0 ? (
            <p data-testid="pirate-holdings-empty">No pirate holdings in this sector.</p>
          ) : (
            <div className="ships-list">
              {pirateHoldings.map((holding) => (
                <div
                  key={holding.id}
                  className="ship-item"
                  data-testid={`pirate-holding-row-${holding.id}`}
                >
                  <span>id: {holding.id}</span>
                  <span>tier: {formatHoldingTier(holding.tier)}</span>
                  <span>owner: {formatHoldingOwner(holding)}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="trading-tips">
          <h3>Station Information</h3>
          <ul>
            <li>Class {stationClass} ports typically trade in {getPortTradingPattern(Number(stationClass))}</li>
            <li>Tax rate affects all transactions: {((port.tax_rate ?? 0) * 100).toFixed(1)}% current rate</li>
            <li>Defense drones: {defenseDrones} protecting the port</li>
            <li>Station shields: {port.port_shields || 0} / 1000 strength</li>
            <li>Click any value above to edit it directly</li>
          </ul>
        </div>
      </div>
    </div>
  );
};

// Helper functions (removed unused getServiceIcon)

const getPortTradingPattern = (portClass: number): string => {
  const patterns: { [key: number]: string } = {
    1: 'basic commodities with limited quantities',
    2: 'standard goods with moderate prices',
    3: 'diverse commodities with good availability',
    4: 'premium goods and specialized equipment',
    5: 'all commodities with best prices and quantities'
  };
  return patterns[portClass] || 'various commodities';
};

export default PortDetail;