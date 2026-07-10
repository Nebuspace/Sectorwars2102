import React, { useEffect, useRef, useState, useCallback } from 'react';
import * as d3 from 'd3';
import { api } from '../../utils/auth';
import './charts.css';

// Matches backend HealthReportResponse (admin_ships.py:67, GET /admin/ships/health-report)
interface MaintenanceShip {
  id: string;
  name: string;
  type: string;
  owner: string;
  sector: string;
  condition_percent: number;
  hull_percent: number;
  status: string;
}

interface CriticalShip extends MaintenanceShip {
  issue: string;
}

interface FleetHealthReportData {
  total_ships: number;
  by_status: Record<string, number>;
  by_condition: Record<string, number>;
  maintenance_needed: MaintenanceShip[];
  critical_issues: CriticalShip[];
}

const STATUS_COLORS = d3.scaleOrdinal<string, string>()
  .range(['#4ECDC4', '#85C1E2', '#F7DC6F', '#FF8C42', '#FF6B6B', '#B39DDB', '#9CCC65']);

const CONDITION_ORDER: { key: string; label: string; color: string }[] = [
  { key: 'excellent', label: 'Excellent', color: '#4ECDC4' },
  { key: 'good', label: 'Good', color: '#85C1E2' },
  { key: 'fair', label: 'Fair', color: '#F7DC6F' },
  { key: 'poor', label: 'Poor', color: '#FF8C42' },
  { key: 'critical', label: 'Critical', color: '#FF6B6B' },
];

const FleetHealthReport: React.FC = () => {
  const [report, setReport] = useState<FleetHealthReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const statusChartRef = useRef<SVGSVGElement>(null);
  const conditionChartRef = useRef<SVGSVGElement>(null);

  const fetchReport = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      // Token attached automatically by the shared api axios interceptor (accessToken).
      const response = await api.get('/api/v1/admin/ships/health-report');
      setReport(response.data as FleetHealthReportData);
    } catch (err) {
      console.error('Error fetching fleet health report:', err);
      setError('Failed to load fleet health report');
      setReport(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchReport();
  }, [fetchReport]);

  useEffect(() => {
    if (report && statusChartRef.current) {
      drawStatusChart(report);
    }
     
  }, [report]);

  useEffect(() => {
    if (report && conditionChartRef.current) {
      drawConditionChart(report);
    }
     
  }, [report]);

  const drawStatusChart = (data: FleetHealthReportData) => {
    const entries = Object.entries(data.by_status)
      .map(([status, count]) => ({ status, count }))
      .filter(d => d.count > 0);

    d3.select(statusChartRef.current).selectAll('*').remove();

    if (entries.length === 0) return;

    const width = 300;
    const height = 300;
    const margin = 40;
    const radius = Math.min(width, height) / 2 - margin;

    const svg = d3.select(statusChartRef.current)
      .attr('width', width)
      .attr('height', height);

    const g = svg.append('g')
      .attr('transform', `translate(${width / 2}, ${height / 2})`);

    const pie = d3.pie<{ status: string; count: number }>().value(d => d.count);
    const arc = d3.arc<d3.PieArcDatum<{ status: string; count: number }>>()
      .innerRadius(0)
      .outerRadius(radius);

    const arcs = g.selectAll('arc')
      .data(pie(entries))
      .enter()
      .append('g');

    arcs.append('path')
      .attr('d', arc)
      .attr('fill', d => STATUS_COLORS(d.data.status))
      .style('stroke', 'white')
      .style('stroke-width', 2)
      .on('mouseover', function (event, d) {
        const tooltip = d3.select('body').append('div')
          .attr('class', 'chart-tooltip')
          .style('opacity', 0);
        tooltip.transition().duration(200).style('opacity', 0.9);
        tooltip.html(`${d.data.status}: ${d.data.count} ships`)
          .style('left', (event.pageX + 10) + 'px')
          .style('top', (event.pageY - 28) + 'px');
      })
      .on('mouseout', function () {
        d3.selectAll('.chart-tooltip').remove();
      });

    arcs.append('text')
      .attr('transform', d => `translate(${arc.centroid(d)})`)
      .attr('text-anchor', 'middle')
      .text(d => d.data.count > 0 ? String(d.data.count) : '')
      .style('fill', 'white')
      .style('font-weight', 'bold')
      .style('font-size', '14px');

    const legend = svg.append('g')
      .attr('transform', `translate(${width - 90}, 20)`);

    const legendItems = legend.selectAll('.legend-item')
      .data(entries)
      .enter()
      .append('g')
      .attr('class', 'legend-item')
      .attr('transform', (_d, i) => `translate(0, ${i * 20})`);

    legendItems.append('rect')
      .attr('width', 15)
      .attr('height', 15)
      .attr('fill', d => STATUS_COLORS(d.status));

    legendItems.append('text')
      .attr('x', 20)
      .attr('y', 12)
      .text(d => d.status)
      .style('font-size', '12px')
      .style('fill', 'var(--text-secondary)');
  };

  const drawConditionChart = (data: FleetHealthReportData) => {
    const chartData = CONDITION_ORDER.map(c => ({
      condition: c.label,
      count: data.by_condition[c.key] ?? 0,
      color: c.color,
    }));

    d3.select(conditionChartRef.current).selectAll('*').remove();

    const margin = { top: 20, right: 30, bottom: 60, left: 60 };
    const width = 400 - margin.left - margin.right;
    const height = 300 - margin.top - margin.bottom;

    const svg = d3.select(conditionChartRef.current)
      .attr('width', width + margin.left + margin.right)
      .attr('height', height + margin.top + margin.bottom);

    const g = svg.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    const x = d3.scaleBand()
      .domain(chartData.map(d => d.condition))
      .range([0, width])
      .padding(0.1);

    const maxCount = d3.max(chartData, d => d.count) ?? 0;
    const y = d3.scaleLinear()
      .domain([0, maxCount > 0 ? maxCount : 1])
      .nice()
      .range([height, 0]);

    g.append('g')
      .attr('class', 'axis axis-x')
      .attr('transform', `translate(0,${height})`)
      .call(d3.axisBottom(x))
      .selectAll('text')
      .style('text-anchor', 'end')
      .attr('dx', '-.8em')
      .attr('dy', '.15em')
      .attr('transform', 'rotate(-45)');

    g.append('g')
      .attr('class', 'axis axis-y')
      .call(d3.axisLeft(y).ticks(5));

    g.append('text')
      .attr('transform', 'rotate(-90)')
      .attr('y', 0 - margin.left)
      .attr('x', 0 - (height / 2))
      .attr('dy', '1em')
      .style('text-anchor', 'middle')
      .style('fill', 'var(--text-secondary)')
      .style('font-size', '12px')
      .text('Number of Ships');

    g.selectAll('.bar')
      .data(chartData)
      .enter().append('rect')
      .attr('class', 'bar')
      .attr('x', d => x(d.condition) as number)
      .attr('y', d => y(d.count))
      .attr('width', x.bandwidth())
      .attr('height', d => height - y(d.count))
      .attr('fill', d => d.color)
      .on('mouseover', function (event, d) {
        const tooltip = d3.select('body').append('div')
          .attr('class', 'chart-tooltip')
          .style('opacity', 0);
        tooltip.transition().duration(200).style('opacity', 0.9);
        const percentage = data.total_ships > 0
          ? ((d.count / data.total_ships) * 100).toFixed(1)
          : '0.0';
        tooltip.html(`${d.condition}: ${d.count} ships (${percentage}%)`)
          .style('left', (event.pageX + 10) + 'px')
          .style('top', (event.pageY - 28) + 'px');
      })
      .on('mouseout', function () {
        d3.selectAll('.chart-tooltip').remove();
      });

    g.selectAll('.bar-label')
      .data(chartData)
      .enter().append('text')
      .attr('class', 'bar-label')
      .attr('x', d => (x(d.condition) as number) + x.bandwidth() / 2)
      .attr('y', d => y(d.count) - 5)
      .attr('text-anchor', 'middle')
      .text(d => d.count > 0 ? String(d.count) : '')
      .style('fill', 'var(--text-primary)')
      .style('font-size', '12px')
      .style('font-weight', 'bold');
  };

  if (loading && !report) {
    return (
      <div className="fleet-health-report">
        <div className="loading-container text-center py-12">
          <div className="loading-spinner mx-auto mb-4"></div>
          <span>Loading fleet health report...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fleet-health-report">
        <div className="alert alert-error mb-6">
          <div className="flex items-center gap-3">
            <span>⚠️</span>
            <span className="flex-1">{error}</span>
            <button onClick={fetchReport} className="btn btn-sm">Retry</button>
          </div>
        </div>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="fleet-health-report">
        <div className="empty-state text-center py-12">
          <span>No fleet health data available.</span>
        </div>
      </div>
    );
  }

  return (
    <div className="fleet-health-report">
      <div className="report-header">
        <h3>Fleet Health Analysis</h3>
        <div className="report-summary">
          <div className="summary-item">
            <span className="summary-label">Total Ships:</span>
            <span className="summary-value">{report.total_ships}</span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Maintenance Needed:</span>
            <span className="summary-value warning">{report.maintenance_needed.length}</span>
          </div>
          <div className="summary-item">
            <span className="summary-label">Critical Issues:</span>
            <span className="summary-value critical">{report.critical_issues.length}</span>
          </div>
          <button onClick={fetchReport} className="btn btn-sm btn-outline">🔄 Refresh</button>
        </div>
      </div>

      <div className="charts-grid">
        <div className="chart-container">
          <h4>Fleet Status Distribution</h4>
          <svg ref={statusChartRef}></svg>
        </div>

        <div className="chart-container">
          <h4>Ship Condition Analysis</h4>
          <svg ref={conditionChartRef}></svg>
        </div>
      </div>

      {report.critical_issues.length > 0 && (
        <div className="critical-issues">
          <h4>⚠️ Critical Issues Requiring Attention</h4>
          <div className="issues-list">
            {report.critical_issues.slice(0, 10).map((issue) => (
              <div key={issue.id} className="issue-card severity-critical">
                <div className="issue-header">
                  <span className="ship-name">{issue.name}</span>
                  <span className="severity-badge critical">
                    {issue.issue.toUpperCase()}
                  </span>
                </div>
                <div className="issue-details">
                  <p className="issue-description">
                    {issue.type} · Owner: {issue.owner} · Sector: {issue.sector}
                  </p>
                  <p className="recommended-action">
                    <strong>Condition:</strong> {issue.condition_percent}% ·{' '}
                    <strong>Hull:</strong> {issue.hull_percent}% ·{' '}
                    <strong>Status:</strong> {issue.status}
                  </p>
                </div>
              </div>
            ))}
          </div>
          {report.critical_issues.length > 10 && (
            <p className="issue-description" style={{ marginTop: '0.5rem' }}>
              Showing 10 of {report.critical_issues.length} critical issues
            </p>
          )}
        </div>
      )}

      {report.maintenance_needed.length > 0 && (
        <div className="maintenance-needed">
          <h4>🔧 Ships Needing Maintenance</h4>
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Owner</th>
                  <th>Sector</th>
                  <th>Condition</th>
                  <th>Hull</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {report.maintenance_needed.slice(0, 25).map((ship) => (
                  <tr key={ship.id}>
                    <td className="font-medium">{ship.name}</td>
                    <td>{ship.type}</td>
                    <td>{ship.owner}</td>
                    <td>{ship.sector}</td>
                    <td>{ship.condition_percent}%</td>
                    <td>{ship.hull_percent}%</td>
                    <td>{ship.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.maintenance_needed.length > 25 && (
            <p className="issue-description" style={{ marginTop: '0.5rem' }}>
              Showing 25 of {report.maintenance_needed.length} ships needing maintenance
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default FleetHealthReport;
