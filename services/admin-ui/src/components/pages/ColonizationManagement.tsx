import React, { useState } from 'react';
import { ColonyOverview } from '../colonization/ColonyOverview';
import { ProductionMonitoring } from '../colonization/ProductionMonitoring';
import { GenesisDeviceTracking } from '../colonization/GenesisDeviceTracking';
import { PlanetaryManagement } from '../colonization/PlanetaryManagement';
import './colonization-management.css';

export const ColonizationManagement: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'colonies' | 'production' | 'genesis' | 'planets'>('colonies');

  return (
    <div className="colonization-management">
      <div className="page-header">
        <h1>Colonization Management</h1>
        <p className="page-description">
          Colony overview, production, genesis devices, and planetary panels
        </p>
      </div>

      <div className="tab-navigation">
        <button
          className={`tab-button ${activeTab === 'colonies' ? 'active' : ''}`}
          onClick={() => setActiveTab('colonies')}
        >
          <span className="tab-icon">🏙️</span>
          Colony Overview
        </button>
        <button
          className={`tab-button ${activeTab === 'production' ? 'active' : ''}`}
          onClick={() => setActiveTab('production')}
        >
          <span className="tab-icon">⚙️</span>
          Production Monitoring
        </button>
        <button
          className={`tab-button ${activeTab === 'genesis' ? 'active' : ''}`}
          onClick={() => setActiveTab('genesis')}
        >
          <span className="tab-icon">🧬</span>
          Genesis Devices
        </button>
        <button
          className={`tab-button ${activeTab === 'planets' ? 'active' : ''}`}
          onClick={() => setActiveTab('planets')}
        >
          <span className="tab-icon">🪐</span>
          Planetary Management
        </button>
      </div>

      <div className="tab-content">
        {activeTab === 'colonies' && <ColonyOverview />}
        {activeTab === 'production' && <ProductionMonitoring />}
        {activeTab === 'genesis' && <GenesisDeviceTracking />}
        {activeTab === 'planets' && <PlanetaryManagement />}
      </div>
    </div>
  );
};