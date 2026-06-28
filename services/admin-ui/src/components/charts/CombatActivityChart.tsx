import React, { useEffect, useRef, useState } from 'react';
import * as d3 from 'd3';

interface CombatActivityChartProps {
  // Loosely typed at the boundary; the real runtime payload is the backend
  // CombatFeedItem (started_at, combat_stats, ...), read defensively below.
  events: any[];
  width?: number;
  height?: number;
}

interface ActivityData {
  time: Date;
  battles: number;
  damage: number;
}

export const CombatActivityChart: React.FC<CombatActivityChartProps> = ({
  events,
  width = 800,
  height = 300
}) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const [activityData, setActivityData] = useState<ActivityData[]>([]);

  useEffect(() => {
    // Process events into time-based activity data
    const now = new Date();
    const hourAgo = new Date(now.getTime() - 60 * 60 * 1000);
    
    // Create 12 5-minute buckets
    const buckets = new Map<string, { battles: number; damage: number }>();
    
    for (let i = 0; i < 12; i++) {
      const time = new Date(hourAgo.getTime() + i * 5 * 60 * 1000);
      const key = time.toISOString().slice(0, 16); // YYYY-MM-DDTHH:MM
      buckets.set(key, { battles: 0, damage: 0 });
    }

    // Aggregate events into buckets
    events.forEach((event: any) => {
      // Real payload (CombatFeedItem) uses started_at + a combat_stats dict.
      // Read both defensively so a non-empty feed can't throw (the previous
      // code read event.timestamp + event.result.* which don't exist).
      const eventTime = new Date(event.started_at ?? event.timestamp);
      if (!isNaN(eventTime.getTime()) && eventTime >= hourAgo && eventTime <= now) {
        const key = eventTime.toISOString().slice(0, 16);
        const bucket = buckets.get(key);
        if (bucket) {
          bucket.battles++;
          bucket.damage += (event.combat_stats?.damageDealt ?? 0) + (event.combat_stats?.damageReceived ?? 0);
        }
      }
    });

    // Convert to array
    const data: ActivityData[] = Array.from(buckets.entries()).map(([timeStr, stats]) => ({
      time: new Date(timeStr),
      battles: stats.battles,
      damage: stats.damage
    }));

    setActivityData(data);
  }, [events]);

  useEffect(() => {
    if (!svgRef.current || activityData.length === 0) return;

    const margin = { top: 20, right: 80, bottom: 40, left: 60 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;

    // Clear previous chart
    d3.select(svgRef.current).selectAll('*').remove();

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height);

    const g = svg.append('g')
      .attr('transform', `translate(${margin.left},${margin.top})`);

    // Scales
    const xScale = d3.scaleTime()
      .domain(d3.extent(activityData, d => d.time) as [Date, Date])
      .range([0, innerWidth]);

    const yScaleBattles = d3.scaleLinear()
      .domain([0, d3.max(activityData, d => d.battles) || 10])
      .nice()
      .range([innerHeight, 0]);

    const yScaleDamage = d3.scaleLinear()
      .domain([0, d3.max(activityData, d => d.damage) || 100000])
      .nice()
      .range([innerHeight, 0]);

    // Line generators
    const battleLine = d3.line<ActivityData>()
      .x(d => xScale(d.time))
      .y(d => yScaleBattles(d.battles))
      .curve(d3.curveMonotoneX);

    const damageLine = d3.line<ActivityData>()
      .x(d => xScale(d.time))
      .y(d => yScaleDamage(d.damage))
      .curve(d3.curveMonotoneX);

    // Add X axis
    g.append('g')
      .attr('transform', `translate(0,${innerHeight})`)
      .call(d3.axisBottom(xScale)
        .tickFormat(d3.timeFormat('%H:%M') as any)
        .ticks(6) as any)
      .append('text')
      .attr('x', innerWidth / 2)
      .attr('y', 35)
      .attr('fill', 'currentColor')
      .style('text-anchor', 'middle')
      .text('Time');

    // Add Y axis for battles (left)
    g.append('g')
      .call(d3.axisLeft(yScaleBattles))
      .append('text')
      .attr('transform', 'rotate(-90)')
      .attr('y', -40)
      .attr('x', -innerHeight / 2)
      .attr('fill', '#e63946')
      .style('text-anchor', 'middle')
      .text('Battles');

    // Add Y axis for damage (right)
    g.append('g')
      .attr('transform', `translate(${innerWidth}, 0)`)
      .call(d3.axisRight(yScaleDamage)
        .tickFormat(d => d3.format('.2s')(d)))
      .append('text')
      .attr('transform', 'rotate(-90)')
      .attr('y', 60)
      .attr('x', -innerHeight / 2)
      .attr('fill', '#f77f00')
      .style('text-anchor', 'middle')
      .text('Damage');

    // Add battle line
    g.append('path')
      .datum(activityData)
      .attr('fill', 'none')
      .attr('stroke', '#e63946')
      .attr('stroke-width', 2)
      .attr('d', battleLine);

    // Add damage line
    g.append('path')
      .datum(activityData)
      .attr('fill', 'none')
      .attr('stroke', '#f77f00')
      .attr('stroke-width', 2)
      .attr('d', damageLine);

    // Add dots for battles
    g.selectAll('.battle-dot')
      .data(activityData)
      .enter().append('circle')
      .attr('class', 'battle-dot')
      .attr('cx', (d: any) => xScale(d.time))
      .attr('cy', (d: any) => yScaleBattles(d.battles))
      .attr('r', 4)
      .attr('fill', '#e63946');

    // Add dots for damage
    g.selectAll('.damage-dot')
      .data(activityData)
      .enter().append('circle')
      .attr('class', 'damage-dot')
      .attr('cx', (d: any) => xScale(d.time))
      .attr('cy', (d: any) => yScaleDamage(d.damage))
      .attr('r', 4)
      .attr('fill', '#f77f00');

    // Add legend
    const legend = g.append('g')
      .attr('transform', `translate(${innerWidth / 2 - 100}, -10)`);

    legend.append('line')
      .attr('x1', 0)
      .attr('x2', 20)
      .attr('y1', 0)
      .attr('y2', 0)
      .attr('stroke', '#e63946')
      .attr('stroke-width', 2);

    legend.append('text')
      .attr('x', 25)
      .attr('y', 4)
      .attr('fill', 'currentColor')
      .text('Battles');

    legend.append('line')
      .attr('x1', 80)
      .attr('x2', 100)
      .attr('y1', 0)
      .attr('y2', 0)
      .attr('stroke', '#f77f00')
      .attr('stroke-width', 2);

    legend.append('text')
      .attr('x', 105)
      .attr('y', 4)
      .attr('fill', 'currentColor')
      .text('Damage');

  }, [activityData, width, height]);

  return (
    <div className="combat-activity-chart">
      <h3>Combat Activity (Last Hour)</h3>
      <svg ref={svgRef}></svg>
    </div>
  );
};