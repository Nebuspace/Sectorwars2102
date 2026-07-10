import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import * as d3 from 'd3';
import PageHeader from '../ui/PageHeader';
import { api } from '../../utils/auth';
import { useEconomyUpdates } from '../../contexts/WebSocketContext';
import { useResourceCatalog } from '../../hooks/useResourceCatalog';
import { useToast, useConfirm } from '../../contexts/ToastContext';
import './economy-dashboard.css';

interface MarketData {
  station_id: string;
  port_name: string;
  sector_name: string;
  commodity: string;
  buy_price: number;
  sell_price: number;
  quantity: number;
  last_updated: string;
}

interface EconomicMetrics {
  total_trade_volume: number;
  total_credits_in_circulation: number;
  average_profit_margin: number;
  most_traded_commodity: string;
  economic_health_score: number;
}

/**
 * A persistent PriceAlert row created via POST /admin/economy/create-alert.
 * The backend exposes create + delete for these rows but no list/GET
 * endpoint, so this dashboard tracks only what it created this session
 * (returned alert_id from the create response) rather than fabricating a
 * full roster.
 */
interface CreatedPriceAlert {
  id: string;
  station_id: string;
  port_name: string;
  commodity: string;
  alert_type: string;
  threshold_value: number;
}

const ALERT_TYPE_OPTIONS = ['price_spike', 'price_drop', 'high_volume', 'low_supply'] as const;

/** Response shape of GET /api/v1/admin/economy/dashboard-summary */
interface DashboardSummary {
  timestamp: string;
  health_score: number;
  daily_summary: {
    total_transactions: number;
    total_volume: number;
    total_value: number;
    unique_traders: number;
  };
  key_metrics: {
    gdp: number;
    money_supply: number;
    market_velocity: number;
    gini_coefficient: number;
  };
  alert_summary: {
    total_alerts: number;
    by_severity: {
      critical: number;
      high: number;
      medium: number;
      low: number;
    };
    critical_alerts: Array<{ severity: string; message: string }>;
  };
  top_trading_ports: Array<{
    station_id: string;
    station_name: string;
    sector_id: string | null;
    transaction_count: number;
    total_volume: number;
    total_value: number;
  }>;
}

/** Price Trends chart - shows buy/sell prices by commodity using D3 grouped bar chart */
const PriceTrendsChart: React.FC<{ marketData: MarketData[] }> = ({ marketData }) => {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!svgRef.current || marketData.length === 0) return;

    // Clear previous
    d3.select(svgRef.current).selectAll('*').remove();

    // Aggregate: average buy/sell price per commodity
    const commodityMap: Record<string, { buyPrices: number[]; sellPrices: number[] }> = {};
    for (const item of marketData) {
      if (!commodityMap[item.commodity]) {
        commodityMap[item.commodity] = { buyPrices: [], sellPrices: [] };
      }
      commodityMap[item.commodity].buyPrices.push(item.buy_price);
      commodityMap[item.commodity].sellPrices.push(item.sell_price);
    }

    const data = Object.entries(commodityMap).map(([commodity, vals]) => ({
      commodity,
      avgBuy: vals.buyPrices.reduce((a, b) => a + b, 0) / vals.buyPrices.length,
      avgSell: vals.sellPrices.reduce((a, b) => a + b, 0) / vals.sellPrices.length
    })).sort((a, b) => b.avgBuy - a.avgBuy);

    if (data.length === 0) return;

    const margin = { top: 20, right: 20, bottom: 60, left: 60 };
    const width = 500 - margin.left - margin.right;
    const height = 280 - margin.top - margin.bottom;

    const svg = d3.select(svgRef.current)
      .attr('width', width + margin.left + margin.right)
      .attr('height', height + margin.top + margin.bottom);

    const g = svg.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    const x0 = d3.scaleBand()
      .domain(data.map(d => d.commodity))
      .rangeRound([0, width])
      .paddingInner(0.2);

    const x1 = d3.scaleBand()
      .domain(['avgBuy', 'avgSell'])
      .rangeRound([0, x0.bandwidth()])
      .padding(0.05);

    const maxPrice = d3.max(data, d => Math.max(d.avgBuy, d.avgSell)) || 100;
    const y = d3.scaleLinear()
      .domain([0, maxPrice * 1.1])
      .rangeRound([height, 0]);

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${height})`)
      .call(d3.axisBottom(x0))
      .selectAll('text')
      .style('text-anchor', 'end')
      .attr('dx', '-0.5em')
      .attr('dy', '0.15em')
      .attr('transform', 'rotate(-35)')
      .attr('fill', '#94a3b8')
      .style('font-size', '11px');

    // Y axis
    g.append('g')
      .call(d3.axisLeft(y).ticks(5).tickFormat(d => d3.format(',.0f')(d as number)))
      .selectAll('text')
      .attr('fill', '#94a3b8');

    g.append('g')
      .call(d3.axisLeft(y).ticks(5).tickSize(-width).tickFormat(() => ''))
      .selectAll('line')
      .attr('stroke', '#334155')
      .attr('stroke-opacity', 0.5);

    // Bars
    const groups = g.selectAll('.bar-group')
      .data(data)
      .enter().append('g')
      .attr('transform', d => `translate(${x0(d.commodity)},0)`);

    groups.append('rect')
      .attr('x', x1('avgBuy') as number)
      .attr('y', d => y(d.avgBuy))
      .attr('width', x1.bandwidth())
      .attr('height', d => height - y(d.avgBuy))
      .attr('fill', '#3b82f6')
      .attr('rx', 2);

    groups.append('rect')
      .attr('x', x1('avgSell') as number)
      .attr('y', d => y(d.avgSell))
      .attr('width', x1.bandwidth())
      .attr('height', d => height - y(d.avgSell))
      .attr('fill', '#10b981')
      .attr('rx', 2);

    // Legend
    const legend = g.append('g')
      .attr('transform', `translate(${width - 140}, -10)`);

    legend.append('rect').attr('width', 12).attr('height', 12).attr('fill', '#3b82f6').attr('rx', 2);
    legend.append('text').attr('x', 16).attr('y', 10).text('Avg Buy').attr('fill', '#94a3b8').style('font-size', '11px');
    legend.append('rect').attr('x', 80).attr('width', 12).attr('height', 12).attr('fill', '#10b981').attr('rx', 2);
    legend.append('text').attr('x', 96).attr('y', 10).text('Avg Sell').attr('fill', '#94a3b8').style('font-size', '11px');

  }, [marketData]);

  if (marketData.length === 0) {
    return <div className="chart-placeholder"><p>No market data available for price trends.</p></div>;
  }

  return <svg ref={svgRef}></svg>;
};

/** Trade Volume chart - shows quantity per port as a horizontal bar chart using D3 */
const TradeVolumeChart: React.FC<{ marketData: MarketData[] }> = ({ marketData }) => {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!svgRef.current || marketData.length === 0) return;

    // Clear previous
    d3.select(svgRef.current).selectAll('*').remove();

    // Aggregate: total quantity per port
    const portMap: Record<string, number> = {};
    for (const item of marketData) {
      const key = item.port_name || item.sector_name || `Port ${item.station_id?.slice(0, 6)}`;
      portMap[key] = (portMap[key] || 0) + item.quantity;
    }

    const data = Object.entries(portMap)
      .map(([port, volume]) => ({ port, volume }))
      .sort((a, b) => b.volume - a.volume)
      .slice(0, 10); // Top 10 ports

    if (data.length === 0) return;

    const margin = { top: 10, right: 30, bottom: 30, left: 120 };
    const width = 500 - margin.left - margin.right;
    const barHeight = 24;
    const height = data.length * (barHeight + 6);

    const svg = d3.select(svgRef.current)
      .attr('width', width + margin.left + margin.right)
      .attr('height', height + margin.top + margin.bottom);

    const g = svg.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    const y = d3.scaleBand()
      .domain(data.map(d => d.port))
      .rangeRound([0, height])
      .padding(0.2);

    const maxVolume = d3.max(data, d => d.volume) || 100;
    const x = d3.scaleLinear()
      .domain([0, maxVolume * 1.1])
      .range([0, width]);

    // Y axis (port names)
    g.append('g')
      .call(d3.axisLeft(y))
      .selectAll('text')
      .attr('fill', '#94a3b8')
      .style('font-size', '11px');

    // X axis
    g.append('g')
      .attr('transform', `translate(0,${height})`)
      .call(d3.axisBottom(x).ticks(5).tickFormat(d => d3.format(',.0f')(d as number)))
      .selectAll('text')
      .attr('fill', '#94a3b8');

    // Grid lines
    g.append('g')
      .call(d3.axisBottom(x).ticks(5).tickSize(-height).tickFormat(() => ''))
      .attr('transform', `translate(0,${height})`)
      .selectAll('line')
      .attr('stroke', '#334155')
      .attr('stroke-opacity', 0.5);

    // Bars
    g.selectAll('.bar')
      .data(data)
      .enter().append('rect')
      .attr('y', d => y(d.port) as number)
      .attr('width', d => x(d.volume))
      .attr('height', y.bandwidth())
      .attr('fill', '#8b5cf6')
      .attr('rx', 3);

    // Value labels
    g.selectAll('.label')
      .data(data)
      .enter().append('text')
      .attr('x', d => x(d.volume) + 5)
      .attr('y', d => (y(d.port) as number) + y.bandwidth() / 2 + 4)
      .text(d => d3.format(',.0f')(d.volume))
      .attr('fill', '#94a3b8')
      .style('font-size', '10px');

  }, [marketData]);

  if (marketData.length === 0) {
    return <div className="chart-placeholder"><p>No market data available for trade volume.</p></div>;
  }

  return <svg ref={svgRef}></svg>;
};

const EconomyDashboard: React.FC = () => {
  const [marketData, setMarketData] = useState<MarketData[]>([]);
  const [metrics, setMetrics] = useState<EconomicMetrics | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [selectedCommodity, setSelectedCommodity] = useState<string>('all');
  const [priceAlerts, setPriceAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  // Persistent price-alert CRUD (WO-ADM-ECONDASH-FE) — session-tracked, see
  // CreatedPriceAlert doc comment above.
  const [createdAlerts, setCreatedAlerts] = useState<CreatedPriceAlert[]>([]);
  const [alertForm, setAlertForm] = useState({
    station_id: '',
    commodity: '',
    alert_type: ALERT_TYPE_OPTIONS[0] as string,
    threshold_value: ''
  });

  const toast = useToast();
  const confirmDialog = useConfirm();

  // Sourced from the resource registry catalog (WO-ARCH-RES-3-FE-CATALOG)
  // instead of the old stale mock list — every value the old list carried
  // (Food/Tech/Minerals/Electronics/Weapons/Medical) never matched a real
  // MarketPrice.commodity value, so this filter was never actually
  // functional. `name` is the wire value market-data's commodity_filter
  // expects (matches models/station.py's DEFAULT_COMMODITIES keys); `label`
  // is the display text. See services/resourceCatalog.ts for the known gap
  // (admin sessions may not be able to reach this endpoint; the filter just
  // degrades to "All Commodities" only, never crashes).
  const { catalog: resourceCatalog, getLabel: getResourceLabel } = useResourceCatalog();
  const commodities = resourceCatalog.map((r) => r.name);

  // Unique stations present in the current market data, for the alert-create
  // station picker (no dedicated station-list endpoint is wired here).
  const stationsInMarket = useMemo(() => {
    const seen = new Map<string, { station_id: string; port_name: string; sector_name: string }>();
    for (const item of marketData) {
      if (!seen.has(item.station_id)) {
        seen.set(item.station_id, {
          station_id: item.station_id,
          port_name: item.port_name,
          sector_name: item.sector_name
        });
      }
    }
    return Array.from(seen.values()).sort((a, b) => a.port_name.localeCompare(b.port_name));
  }, [marketData]);

  const commoditiesAtSelectedStation = useMemo(() => {
    if (!alertForm.station_id) return [];
    return Array.from(new Set(
      marketData
        .filter(item => item.station_id === alertForm.station_id)
        .map(item => item.commodity)
    ));
  }, [marketData, alertForm.station_id]);

  // WebSocket handlers
  const handleMarketUpdate = useCallback((data: any) => {
    setMarketData(prevData => {
      // Update or add the new market data
      const index = prevData.findIndex(item => 
        item.station_id === data.station_id && item.commodity === data.commodity
      );
      
      if (index >= 0) {
        const newData = [...prevData];
        newData[index] = { ...newData[index], ...data };
        return newData;
      } else {
        return [...prevData, data];
      }
    });
    setLastUpdate(new Date());
  }, []);

  const handlePriceChange = useCallback((data: any) => {
    setPriceAlerts(prev => [data, ...prev].slice(0, 10)); // Keep last 10 alerts
  }, []);

  const handleIntervention = useCallback((_data: any) => {
    // Refresh market data after intervention
    fetchEconomicData();
  }, []);

  // Subscribe to WebSocket events
  useEconomyUpdates(handleMarketUpdate, handlePriceChange, handleIntervention);

  useEffect(() => {
    fetchEconomicData();
    const interval = setInterval(fetchEconomicData, 60000); // Update every 60 seconds as backup
    
    return () => {
      clearInterval(interval);
    };
  }, [selectedCommodity]);


  const fetchEconomicData = async () => {
    setLoading(true);
    setError(null);

    // Fetch all economy data concurrently - use allSettled so partial failures don't blank everything
    const [marketRes, metricsRes, alertsRes, summaryRes] = await Promise.allSettled([
      api.get('/api/v1/admin/economy/market-data', {
        params: {
          commodity_filter: selectedCommodity !== 'all' ? selectedCommodity : undefined,
          limit: 100
        }
      }),
      api.get('/api/v1/admin/economy/metrics', {
        params: { time_period: '24h' }
      }),
      api.get('/api/v1/admin/economy/price-alerts'),
      api.get('/api/v1/admin/economy/dashboard-summary')
    ]);

    // Track errors for display
    const errors: string[] = [];

    // Process market data
    if (marketRes.status === 'fulfilled') {
      setMarketData(marketRes.value.data as MarketData[]);
    } else {
      setMarketData([]);
      errors.push('Market data unavailable');
    }

    // Process economic metrics
    if (metricsRes.status === 'fulfilled') {
      setMetrics(metricsRes.value.data as EconomicMetrics);
    } else {
      setMetrics(null);
      errors.push('Economic metrics unavailable');
    }

    // Process price alerts
    if (alertsRes.status === 'fulfilled') {
      setPriceAlerts(Array.isArray(alertsRes.value.data) ? alertsRes.value.data : []);
    } else {
      setPriceAlerts([]);
    }

    // Process economic health summary (non-blocking - its own honest empty state)
    if (summaryRes.status === 'fulfilled') {
      setSummary(summaryRes.value.data as DashboardSummary);
    } else {
      setSummary(null);
    }

    // Show combined error if all endpoints failed
    if (errors.length === 2) {
      setError('Failed to load economic data. Please check if the gameserver is running.');
    } else if (errors.length > 0) {
      setError(errors.join(' | '));
    }

    setLoading(false);
  };

  const handlePriceIntervention = async (stationId: string, commodity: string, oldPrice: number, newPrice: number) => {
    const ok = await confirmDialog({
      title: 'Adjust market price',
      message: `Set ${commodity} buy price from ${oldPrice.toLocaleString()} to ${newPrice.toLocaleString()} credits at this station?`
    });
    if (!ok) return;

    try {
      const response = await api.post('/api/v1/admin/economy/intervention', {
        intervention_type: 'price_adjustment',
        parameters: {
          station_id: stationId,
          resource_type: commodity,
          new_price: newPrice
        }
      });
      if (response.status === 200) {
        toast.success(`${commodity} buy price updated to ${newPrice.toLocaleString()} credits`);
        fetchEconomicData();
      } else {
        toast.error('Price intervention failed.');
      }
    } catch (error: any) {
      toast.error(error.response?.data?.detail || 'Price intervention failed.');
    }
  };

  const handleInjectSupply = async (stationId: string, commodity: string, portName: string) => {
    const amountStr = prompt(`Units of ${commodity} to inject at ${portName}:`, '100');
    if (amountStr === null) return;
    const amount = parseInt(amountStr, 10);
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error('Enter a valid positive quantity.');
      return;
    }

    const ok = await confirmDialog({
      title: 'Inject market supply',
      message: `Inject ${amount.toLocaleString()} units of ${commodity} into ${portName}'s stock?`
    });
    if (!ok) return;

    try {
      const response = await api.post('/api/v1/admin/economy/intervention', {
        intervention_type: 'inject_liquidity',
        parameters: {
          station_id: stationId,
          resources: { [commodity]: amount }
        }
      });
      if (response.status === 200) {
        toast.success(`Injected ${amount.toLocaleString()} units of ${commodity} at ${portName}`);
        fetchEconomicData();
      } else {
        toast.error('Supply injection failed.');
      }
    } catch (error: any) {
      toast.error(error.response?.data?.detail || 'Supply injection failed.');
    }
  };

  const handleCreateAlert = async (e: React.FormEvent) => {
    e.preventDefault();
    const { station_id, commodity, alert_type, threshold_value } = alertForm;
    if (!station_id || !commodity || !threshold_value) {
      toast.error('Station, commodity, and threshold are required.');
      return;
    }
    const thresholdNum = parseFloat(threshold_value);
    if (!Number.isFinite(thresholdNum)) {
      toast.error('Threshold must be a number.');
      return;
    }

    const station = stationsInMarket.find(s => s.station_id === station_id);
    const portName = station?.port_name || station_id;

    const ok = await confirmDialog({
      title: 'Create price alert',
      message: `Create a "${alert_type}" alert for ${commodity} at ${portName} with threshold ${thresholdNum}?`
    });
    if (!ok) return;

    try {
      const response = await api.post('/api/v1/admin/economy/create-alert', {
        station_id,
        commodity,
        alert_type,
        threshold_value: thresholdNum
      });
      if (response.status === 200) {
        setCreatedAlerts(prev => [
          {
            id: response.data.alert_id,
            station_id,
            port_name: portName,
            commodity,
            alert_type,
            threshold_value: thresholdNum
          },
          ...prev
        ]);
        toast.success('Price alert created successfully');
        setAlertForm({ station_id: '', commodity: '', alert_type: ALERT_TYPE_OPTIONS[0], threshold_value: '' });
        fetchEconomicData();
      } else {
        toast.error('Failed to create price alert');
      }
    } catch (error: any) {
      toast.error(error.response?.data?.detail || 'Failed to create price alert');
    }
  };

  const handleDeleteAlert = async (alert: CreatedPriceAlert) => {
    const ok = await confirmDialog({
      title: 'Delete price alert',
      message: `Delete the ${alert.alert_type} alert for ${alert.commodity} at ${alert.port_name}?`,
      danger: true
    });
    if (!ok) return;

    try {
      const response = await api.delete(`/api/v1/admin/economy/alerts/${alert.id}`);
      if (response.status === 200) {
        setCreatedAlerts(prev => prev.filter(a => a.id !== alert.id));
        toast.success('Price alert deleted successfully');
        fetchEconomicData();
      } else {
        toast.error('Failed to delete price alert');
      }
    } catch (error: any) {
      toast.error(error.response?.data?.detail || 'Failed to delete price alert');
    }
  };

  const filteredMarketData = selectedCommodity === 'all' 
    ? marketData 
    : marketData.filter(item => item.commodity === selectedCommodity);

  return (
    <div className="economy-dashboard">
      <PageHeader 
        title="Economy Dashboard" 
        subtitle="Monitor and manage the galactic economy"
      />
      
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
      
      {loading ? (
        <div className="loading-spinner">Loading economic data...</div>
      ) : (
        <>
          {/* Error Notice */}
          {error && (
            <div className="alert error" style={{ marginBottom: '20px' }}>
              <span className="alert-icon">❌</span>
              <span className="alert-message">
                {error}
              </span>
            </div>
          )}
          
          {/* Economic Health Metrics */}
          <div className="metrics-grid">
            {metrics && (
              <>
                <div className="metric-card">
                  <h3>Trade Volume</h3>
                  <span className="metric-value">{metrics.total_trade_volume.toLocaleString()}</span>
                  <span className="metric-label">Credits/Day</span>
                </div>
                <div className="metric-card">
                  <h3>Credits in Circulation</h3>
                  <span className="metric-value">{metrics.total_credits_in_circulation.toLocaleString()}</span>
                  <span className="metric-label">Total Credits</span>
                </div>
                <div className="metric-card">
                  <h3>Average Profit Margin</h3>
                  <span className="metric-value">{metrics.average_profit_margin.toFixed(1)}%</span>
                  <span className="metric-label">Across All Routes</span>
                </div>
                <div className="metric-card">
                  <h3>Economic Health</h3>
                  <span className={`metric-value ${metrics.economic_health_score > 70 ? 'healthy' : 'warning'}`}>
                    {metrics.economic_health_score.toFixed(0)}%
                  </span>
                  <span className="metric-label">Overall Score</span>
                </div>
              </>
            )}
          </div>

          {/* Economic Health (from dashboard-summary) */}
          <div className="health-section">
            <h3>Economic Health Snapshot</h3>
            {summary ? (
              <>
                <div className="metrics-grid">
                  <div className="metric-card">
                    <h3>Health Score</h3>
                    <span className={`metric-value ${summary.health_score >= 70 ? 'healthy' : 'warning'}`}>
                      {summary.health_score.toFixed(0)}
                    </span>
                    <span className="metric-label">Overall Index (0-100)</span>
                  </div>
                  <div className="metric-card">
                    <h3>Gini Coefficient</h3>
                    <span className={`metric-value ${summary.key_metrics.gini_coefficient <= 0.4 ? 'healthy' : 'warning'}`}>
                      {summary.key_metrics.gini_coefficient.toFixed(3)}
                    </span>
                    <span className="metric-label">Wealth Inequality (0-1)</span>
                  </div>
                  <div className="metric-card">
                    <h3>Market Velocity</h3>
                    <span className="metric-value">{summary.key_metrics.market_velocity.toFixed(2)}</span>
                    <span className="metric-label">Turnover Rate</span>
                  </div>
                  <div className="metric-card">
                    <h3>Money Supply (M2)</h3>
                    <span className="metric-value">{summary.key_metrics.money_supply.toLocaleString()}</span>
                    <span className="metric-label">Total in circulation</span>
                  </div>
                  <div className="metric-card">
                    <h3>GDP</h3>
                    <span className="metric-value">{summary.key_metrics.gdp.toLocaleString()}</span>
                    <span className="metric-label">Gross Domestic Product</span>
                  </div>
                  <div className="metric-card">
                    <h3>Unique Traders</h3>
                    <span className="metric-value">{summary.daily_summary.unique_traders.toLocaleString()}</span>
                    <span className="metric-label">Active (24h)</span>
                  </div>
                  <div className="metric-card">
                    <h3>Transactions</h3>
                    <span className="metric-value">{summary.daily_summary.total_transactions.toLocaleString()}</span>
                    <span className="metric-label">Volume (24h)</span>
                  </div>
                  <div className="metric-card">
                    <h3>Active Alerts</h3>
                    <span className={`metric-value ${summary.alert_summary.by_severity.critical > 0 ? 'warning' : 'healthy'}`}>
                      {summary.alert_summary.total_alerts.toLocaleString()}
                    </span>
                    <span className="metric-label">
                      {summary.alert_summary.by_severity.critical} critical / {summary.alert_summary.by_severity.high} high
                    </span>
                  </div>
                </div>
                <div className="health-meta">
                  Snapshot as of {new Date(summary.timestamp).toLocaleString()}
                </div>
              </>
            ) : (
              <div className="health-empty">
                Economic health summary is unavailable.
              </div>
            )}
          </div>

          {/* Price Alerts */}
          {priceAlerts.length > 0 && (
            <div className="alerts-section">
              <h3>Price Alerts</h3>
              <div className="alerts-list">
                {priceAlerts.map((alert, index) => (
                  <div key={index} className={`alert ${alert.severity}`}>
                    <span className="alert-icon">⚠️</span>
                    <span className="alert-message">{alert.message}</span>
                    <span className="alert-time">{alert.timestamp}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Manage Price Alerts (persistent PriceAlert CRUD) */}
          <div className="alert-manage-section">
            <h3>Manage Price Alerts</h3>
            <form className="alert-create-form" onSubmit={handleCreateAlert}>
              <div className="alert-form-grid">
                <div className="form-group">
                  <label htmlFor="alert-station">Station</label>
                  <select
                    id="alert-station"
                    value={alertForm.station_id}
                    onChange={(e) => setAlertForm({ ...alertForm, station_id: e.target.value, commodity: '' })}
                  >
                    <option value="">Select a station…</option>
                    {stationsInMarket.map(station => (
                      <option key={station.station_id} value={station.station_id}>
                        {station.port_name} ({station.sector_name})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="alert-commodity">Commodity</label>
                  <select
                    id="alert-commodity"
                    value={alertForm.commodity}
                    onChange={(e) => setAlertForm({ ...alertForm, commodity: e.target.value })}
                    disabled={!alertForm.station_id}
                  >
                    <option value="">Select a commodity…</option>
                    {commoditiesAtSelectedStation.map(commodity => (
                      <option key={commodity} value={commodity}>{getResourceLabel(commodity)}</option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="alert-type">Alert Type</label>
                  <select
                    id="alert-type"
                    value={alertForm.alert_type}
                    onChange={(e) => setAlertForm({ ...alertForm, alert_type: e.target.value })}
                  >
                    {ALERT_TYPE_OPTIONS.map(type => (
                      <option key={type} value={type}>{type.replace('_', ' ')}</option>
                    ))}
                  </select>
                </div>

                <div className="form-group">
                  <label htmlFor="alert-threshold">Threshold Value</label>
                  <input
                    id="alert-threshold"
                    type="number"
                    step="any"
                    value={alertForm.threshold_value}
                    onChange={(e) => setAlertForm({ ...alertForm, threshold_value: e.target.value })}
                    placeholder="e.g. 15"
                  />
                </div>
              </div>

              <div className="alert-form-actions">
                <button type="submit" className="refresh-btn">➕ Create Alert</button>
              </div>
            </form>

            {createdAlerts.length > 0 ? (
              <div className="created-alerts-list">
                {createdAlerts.map(alert => (
                  <div key={alert.id} className="created-alert-item">
                    <div className="created-alert-meta">
                      <span className="commodity-badge">{alert.commodity}</span>
                      <span>{alert.alert_type.replace('_', ' ')} @ {alert.port_name}</span>
                      <span className="alert-time">threshold {alert.threshold_value}</span>
                    </div>
                    <button
                      className="action-btn delete"
                      onClick={() => handleDeleteAlert(alert)}
                    >
                      🗑️ Delete
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="health-empty">
                No alerts created this session yet.
              </div>
            )}
          </div>

          {/* Market Data Controls */}
          <div className="market-controls">
            <div className="commodity-filter">
              <label htmlFor="commodity-select">Filter by Commodity:</label>
              <select 
                id="commodity-select"
                value={selectedCommodity} 
                onChange={(e) => setSelectedCommodity(e.target.value)}
              >
                <option value="all">All Commodities</option>
                {commodities.map(commodity => (
                  <option key={commodity} value={commodity}>{getResourceLabel(commodity)}</option>
                ))}
              </select>
            </div>
            
            <button onClick={fetchEconomicData} className="refresh-btn">
              🔄 Refresh Data
            </button>
          </div>

          {/* Market Data Table */}
          <div className="market-data-section">
            <h3>Market Data</h3>
            <div className="market-table-container">
              <table className="market-table">
                <thead>
                  <tr>
                    <th>Port</th>
                    <th>Sector</th>
                    <th>Commodity</th>
                    <th>Buy Price</th>
                    <th>Sell Price</th>
                    <th>Quantity</th>
                    <th>Profit Margin</th>
                    <th>Last Updated</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredMarketData.map((item, index) => {
                    const profitMargin = ((item.sell_price - item.buy_price) / item.buy_price * 100);
                    return (
                      <tr key={index}>
                        <td data-label="Port">{item.port_name}</td>
                        <td data-label="Sector">{item.sector_name}</td>
                        <td data-label="Commodity">
                          <span className={`commodity-badge ${item.commodity.toLowerCase()}`}>
                            {item.commodity}
                          </span>
                        </td>
                        <td data-label="Buy Price" className="price">{item.buy_price.toLocaleString()}</td>
                        <td data-label="Sell Price" className="price">{item.sell_price.toLocaleString()}</td>
                        <td data-label="Quantity">{item.quantity.toLocaleString()}</td>
                        <td data-label="Profit Margin" className={`profit-margin ${profitMargin > 20 ? 'high' : profitMargin > 10 ? 'medium' : 'low'}`}>
                          {profitMargin.toFixed(1)}%
                        </td>
                        <td data-label="Last Updated">{new Date(item.last_updated).toLocaleTimeString()}</td>
                        <td data-label="Actions">
                          <div className="action-btn-group">
                            <button
                              className="action-btn intervention"
                              onClick={() => {
                                const newPriceStr = prompt(`Set new buy price for ${item.commodity}:`, item.buy_price.toString());
                                if (newPriceStr === null) return;
                                const newPrice = parseFloat(newPriceStr);
                                if (!Number.isFinite(newPrice) || newPrice <= 0) {
                                  toast.error('Enter a valid positive price.');
                                  return;
                                }
                                handlePriceIntervention(item.station_id, item.commodity, item.buy_price, newPrice);
                              }}
                            >
                              💱 Intervene
                            </button>
                            <button
                              className="action-btn inject"
                              onClick={() => handleInjectSupply(item.station_id, item.commodity, item.port_name)}
                            >
                              📦 Inject
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Economic Analysis Charts */}
          <div className="charts-section">
            <div className="chart-container">
              <h3>Price Trends (24h)</h3>
              <PriceTrendsChart marketData={filteredMarketData} />
            </div>

            <div className="chart-container">
              <h3>Trade Volume by Route</h3>
              <TradeVolumeChart marketData={filteredMarketData} />
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default EconomyDashboard;