import React, { useState } from 'react';
import { api } from '../../utils/auth';
import './universe-detail.css';

interface PortDetailProps {
  port: any;
  onBack: () => void;
  onUpdate?: (updatedPort: any) => void;
}

const PortDetail: React.FC<PortDetailProps> = ({ port, onBack, onUpdate }) => {
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<any>({});
  const [isLoading, setIsLoading] = useState(false);

  const handleEdit = (field: string, currentValue: any) => {
    setEditingField(field);
    setEditValues({ ...editValues, [field]: currentValue });
  };

  const handleSave = async (field: string) => {
    try {
      setIsLoading(true);
      const value = editValues[field];
      
      // Update port via API
      await api.patch(`/api/v1/admin/ports/${port.id}`, {
        [field]: value
      });
      
      // Update local state
      const updatedPort = { ...port, [field]: value };
      if (onUpdate) {
        onUpdate(updatedPort);
      }
      
      setEditingField(null);
    } catch (error) {
      console.error(`Failed to update ${field}:`, error);
      alert(`Failed to update ${field}`);
    } finally {
      setIsLoading(false);
    }
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

  const classInfo = getPortClassInfo(port.port_class);

  return (
    <div className="port-detail">
      <div className="detail-header">
        <button className="back-button" onClick={onBack}>
          ← Back to Sector
        </button>
        <h2>🏪 {port.name}</h2>
        <div className="port-class" style={{ backgroundColor: classInfo.color }}>
          Class {port.port_class}: {classInfo.name}
        </div>
      </div>

      <div className="detail-content">
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
                  field="port_class" 
                  value={port.port_class} 
                  type="select"
                  options={['1', '2', '3', '4', '5']}
                />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Owner:</span>
              <span className="value">
                <EditableField field="owner_name" value={port.owner_name || 'Federation'} type="text" />
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
                <EditableField field="defense_fighters" value={port.defense_fighters} type="number" />
              </span>
            </div>
            <div className="info-item">
              <span className="label">Purchase Price:</span>
              <span className="value">{(port.port_class * 250000).toLocaleString()} credits</span>
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
          </div>
        </div>

        <div className="services-section">
          <h3>Station Services Port Services & Equipment Equipment</h3>
          <div className="services-grid">
            <div className="service-item">
              <span className="service-icon">🛡️</span>
              <span className="service-name">Station Shields</span>
              <span className="service-status">
                <EditableField 
                  field="port_shields" 
                  value={port.port_shields || 0} 
                  type="number" 
                /> / 1000
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">🤖</span>
              <span className="service-name">Defense Drones</span>
              <span className="service-status">
                <EditableField 
                  field="defense_fighters" 
                  value={port.defense_fighters || 0} 
                  type="number" 
                />
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">🔧</span>
              <span className="service-name">Max Maintenance</span>
              <span className="service-status">
                <EditableField 
                  field="max_maintenance" 
                  value={port.max_maintenance || 100} 
                  type="number" 
                />%
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">💰</span>
              <span className="service-name">Buy Rate</span>
              <span className="service-status">
                <EditableField 
                  field="buy_rate" 
                  value={port.buy_rate || 95} 
                  type="number" 
                />%
              </span>
            </div>
            <div className="service-item">
              <span className="service-icon">💸</span>
              <span className="service-name">Sell Rate</span>
              <span className="service-status">
                <EditableField 
                  field="sell_rate" 
                  value={port.sell_rate || 105} 
                  type="number" 
                />%
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
                  const newFighters = prompt('Enter new defense drone count:', port.defense_fighters?.toString() || '100');
                  if (newFighters) handleEdit('defense_fighters', parseInt(newFighters));
                }}
              >
                🤖 Deploy Defense Drones
              </button>
              <button 
                className="admin-action-btn"
                onClick={() => {
                  const newShields = prompt('Enter new shield strength:', port.port_shields?.toString() || '500');
                  if (newShields) handleEdit('port_shields', parseInt(newShields));
                }}
              >
                🛡️ Adjust Station Shields
              </button>
            </div>
          </div>
        </div>
        
        <div className="trading-tips">
          <h3>Station Information</h3>
          <ul>
            <li>Class {port.port_class} ports typically trade in {getPortTradingPattern(port.port_class)}</li>
            <li>Tax rate affects all transactions: {((port.tax_rate ?? 0) * 100).toFixed(1)}% current rate</li>
            <li>Defense drones: {port.defense_fighters} protecting the port</li>
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