import React, { useEffect, useRef, useState } from 'react';
import { useSettings } from '../../contexts/SettingsContext';
import './sector-viewport.css';

interface SectorViewportProps {
  sectorType?: string;
  sectorName?: string;
  hazardLevel?: number;
  radiationLevel?: number;
  stations?: any[];
  planets?: any[];
  width?: number;
  height?: number;
  onEntityClick?: (entity: { type: 'station' | 'planet'; id: string; name: string }) => void;
}

interface OrbitalBody {
  planetIndex: number;
  orbitRadius: number;
  orbitSpeed: number;
  orbitOffset: number;
}

const SectorViewport: React.FC<SectorViewportProps> = ({
  sectorType = 'normal',
  sectorName = 'Unknown Sector',
  hazardLevel = 0,
  radiationLevel = 0,
  stations = [],
  planets = [],
  width = 450,
  height = 300,
  onEntityClick
}) => {
  // Global UI scale (CSS `zoom` on #root). 1.0 = 100% (no-op default). Read so
  // pointer hit-testing stays correct when the whole UI is zoomed.
  const { uiScale } = useSettings();

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationFrameRef = useRef<number | undefined>(undefined);
  const [isAnimating, setIsAnimating] = useState(true);
  const [hoveredEntity, setHoveredEntity] = useState<{ type: 'station' | 'planet'; name: string; x: number; y: number } | null>(null);

  // Store entity positions for hit detection
  const entityPositionsRef = useRef<Array<{ type: 'station' | 'planet'; id: string; name: string; x: number; y: number; radius: number }>>([]);

  // Store static starfield (generated once)
  const starfieldRef = useRef<Array<{ x: number; y: number; size: number; brightness: number }>>([]);

  // Initialize static starfield once
  useEffect(() => {
    if (starfieldRef.current.length === 0) {
      starfieldRef.current = generateStarfield(width, height);
    }
  }, [width, height]);

  // Animation loop
  useEffect(() => {
    if (!isAnimating) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const animate = () => {
      // Clear background completely (deep space black)
      ctx.fillStyle = '#050810';
      ctx.fillRect(0, 0, width, height);

      // Draw static starfield background
      drawStarfield(ctx, starfieldRef.current);

      // Clear entity positions for this frame
      entityPositionsRef.current = [];

      // Draw planets (larger, static positions)
      drawPlanetsEnhanced(ctx, planets, width, height, entityPositionsRef.current);

      // Draw stations orbiting their planets (animated)
      drawStationsOrbiting(ctx, stations, planets, width, height, entityPositionsRef.current);

      // Draw sector-specific effects
      drawSectorEffects(ctx, sectorType, width, height, hazardLevel, radiationLevel);

      animationFrameRef.current = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [isAnimating, sectorType, hazardLevel, radiationLevel, stations, planets, width, height]);

  // Mouse event handlers for interactivity
  const handleMouseMove = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const { x: mouseX, y: mouseY } = toBufferCoords(canvas, rect, event.clientX, event.clientY, uiScale);

    // Display-space pointer offset (CSS pixels relative to the canvas box). The
    // tooltip lives in `.viewport-overlay`, a display-space overlay positioned
    // via `left`/`top` in px, so it must be placed from these coords — NOT the
    // drawing-buffer coords above (those are for hit-testing only). At
    // uiScale=1 with buffer == display size these coincide; at scale≠1 they
    // diverge by the buffer/display ratio, which is exactly the offset we drop.
    const displayX = event.clientX - rect.left;
    const displayY = event.clientY - rect.top;

    // Check if mouse is over any entity
    const hoveredItem = entityPositionsRef.current.find(entity => {
      const dx = mouseX - entity.x;
      const dy = mouseY - entity.y;
      const distance = Math.sqrt(dx * dx + dy * dy);
      return distance <= entity.radius;
    });

    if (hoveredItem) {
      setHoveredEntity({ type: hoveredItem.type, name: hoveredItem.name, x: displayX, y: displayY });
      canvas.style.cursor = 'pointer';
    } else {
      setHoveredEntity(null);
      canvas.style.cursor = 'default';
    }
  };

  const handleClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (hoveredEntity && onEntityClick) {
      const entity = entityPositionsRef.current.find(e => e.name === hoveredEntity.name);
      if (entity) {
        onEntityClick({ type: entity.type, id: entity.id, name: entity.name });
      }
    }
  };

  return (
    <div className="sector-viewport-container">
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className="sector-viewport-canvas"
        onMouseMove={handleMouseMove}
        onClick={handleClick}
      />
      <div className="viewport-overlay">
        <div className="viewport-label">{sectorName}</div>
        {hoveredEntity && (
          <div
            className="viewport-tooltip"
            style={{
              left: `${hoveredEntity.x + 10}px`,
              top: `${hoveredEntity.y + 10}px`
            }}
          >
            <div className="tooltip-type">{hoveredEntity.type === 'station' ? '🏢 STATION' : '🪐 PLANET'}</div>
            <div className="tooltip-name">{hoveredEntity.name}</div>
          </div>
        )}
      </div>
      {/* Legend */}
      <div className="viewport-legend">
        <div className="legend-item">
          <div className="legend-icon planet-icon">●</div>
          <div className="legend-label">Planets</div>
        </div>
        <div className="legend-item">
          <div className="legend-icon station-icon">⬡</div>
          <div className="legend-label">Stations</div>
        </div>
      </div>
    </div>
  );
};

// Helper functions

/**
 * Map a pointer position (clientX/clientY) into the canvas DRAWING-BUFFER
 * coordinate space, where all entity hit-test positions are stored
 * (0..canvas.width, 0..canvas.height).
 *
 * The canvas is sized by CSS (`width: 100%`), so its displayed size differs
 * from its buffer size in general. The correct conversion scales the pointer
 * offset by the buffer/display ratio:
 *
 *     bufferX = (clientX - rect.left) * (canvas.width  / rect.width)
 *     bufferY = (clientY - rect.top)  * (canvas.height / rect.height)
 *
 * Why this is correct under the global CSS `zoom: var(--ui-scale)` on #root:
 * `getBoundingClientRect()` reports the element box in the SAME (zoomed) layout
 * space that `event.clientX/Y` live in. So at zoom `s`, both the numerator
 * (clientX - rect.left) and the denominator (rect.width) are scaled by `s`,
 * and `s` cancels in the ratio — the mapping is correct at any uiScale without
 * an explicit `s` term. (`uiScale` is accepted so callers express intent and so
 * the no-op-at-1.0 contract is verifiable; it is only used as a guarded
 * fallback below.)
 *
 * No-op proof at uiScale === 1: when the canvas is displayed at its natural
 * buffer size, rect.width === canvas.width and rect.height === canvas.height,
 * so scaleX === scaleY === 1 and the result is exactly
 * (clientX - rect.left, clientY - rect.top) — identical to the prior code.
 *
 * The `uiScale === 1` early-return makes that no-op exact-by-construction (no
 * floating-point ratio at all at 100%), and is a defensive guard for the
 * degenerate case where a freshly-mounted / hidden canvas reports a zero-size
 * rect (ratio would divide by zero) before first layout.
 */
function toBufferCoords(
  canvas: HTMLCanvasElement,
  rect: DOMRect,
  clientX: number,
  clientY: number,
  uiScale: number
): { x: number; y: number } {
  const offsetX = clientX - rect.left;
  const offsetY = clientY - rect.top;

  // Strict no-op at 100%: identical to the original (clientX - rect.left) math.
  if (uiScale === 1 || rect.width === 0 || rect.height === 0) {
    return { x: offsetX, y: offsetY };
  }

  // Buffer/display ratio inherently cancels the CSS `zoom` (see proof above).
  return {
    x: offsetX * (canvas.width / rect.width),
    y: offsetY * (canvas.height / rect.height),
  };
}

function generateStarfield(width: number, height: number) {
  const stars: Array<{ x: number; y: number; size: number; brightness: number }> = [];
  const starCount = 200;

  for (let i = 0; i < starCount; i++) {
    stars.push({
      x: Math.random() * width,
      y: Math.random() * height,
      size: Math.random() * 1.5 + 0.3,
      brightness: Math.random() * 0.5 + 0.3
    });
  }

  return stars;
}

function drawStarfield(
  ctx: CanvasRenderingContext2D,
  stars: Array<{ x: number; y: number; size: number; brightness: number }>
) {
  // Draw static stars
  stars.forEach(star => {
    ctx.globalAlpha = star.brightness;
    ctx.fillStyle = '#ffffff';
    ctx.beginPath();
    ctx.arc(star.x, star.y, star.size, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.globalAlpha = 1.0;
}

// Planet rendering with labels and position tracking
function drawPlanetsEnhanced(
  ctx: CanvasRenderingContext2D,
  planets: any[],
  width: number,
  height: number,
  entityPositions: Array<{ type: 'station' | 'planet'; id: string; name: string; x: number; y: number; radius: number }>
) {
  if (!planets || planets.length === 0) return;

  // Position planets across viewport with proper spacing for up to 4 planets
  const planetCount = planets.length;
  const spacing = width / (planetCount + 1);

  planets.forEach((planet, index) => {
    // Position planets evenly spaced horizontally, centered vertically
    const x = spacing * (index + 1);
    const y = height * 0.5;
    const radius = 35; // Larger base radius for planets

    // Track position for hit detection
    entityPositions.push({
      type: 'planet',
      id: planet.id,
      name: planet.name,
      x,
      y,
      radius: radius + 10 // Larger hit area
    });

    // Planet body with radial gradient
    const gradient = ctx.createRadialGradient(
      x - radius * 0.3,
      y - radius * 0.3,
      radius * 0.1,
      x,
      y,
      radius
    );

    // Color based on planet type
    const planetColors = {
      'terran': { start: '#00ff41', mid: '#00d9ff', end: '#004d19' },
      'ice': { start: '#88ddff', mid: '#00d9ff', end: '#001a33' },
      'volcanic': { start: '#ff6b00', mid: '#ff0000', end: '#330000' },
      'gas_giant': { start: '#ffb000', mid: '#c961de', end: '#1a0033' },
      'barren': { start: '#8b4513', mid: '#696969', end: '#1a1a1a' },
      'oceanic': { start: '#00d9ff', mid: '#0066cc', end: '#001a33' },
      'desert': { start: '#ffcc66', mid: '#cc9933', end: '#663300' },
      'jungle': { start: '#00ff41', mid: '#00cc33', end: '#004d19' }
    };

    const planetType = planet.type?.toLowerCase().replace('planettype.', '') || 'barren';
    const colors = planetColors[planetType as keyof typeof planetColors] || planetColors['barren'];

    gradient.addColorStop(0, colors.start);
    gradient.addColorStop(0.5, colors.mid);
    gradient.addColorStop(1, colors.end);

    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();

    // Atmosphere glow
    ctx.strokeStyle = colors.start;
    ctx.globalAlpha = 0.4;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.arc(x, y, radius + 3, 0, Math.PI * 2);
    ctx.stroke();
    ctx.globalAlpha = 1.0;

    // Draw label
    ctx.font = 'bold 12px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    // Label background
    const labelY = y + radius + 12;
    const labelWidth = ctx.measureText(planet.name).width + 10;
    ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
    ctx.fillRect(x - labelWidth / 2, labelY, labelWidth, 18);

    // Label text
    ctx.fillStyle = colors.start;
    ctx.fillText(planet.name, x, labelY + 3);
  });
}

// Draw stations - orbiting planets if available, or standalone if no planets
function drawStationsOrbiting(
  ctx: CanvasRenderingContext2D,
  stations: any[],
  planets: any[],
  width: number,
  height: number,
  entityPositions: Array<{ type: 'station' | 'planet'; id: string; name: string; x: number; y: number; radius: number }>
) {
  if (!stations || stations.length === 0) return;

  const planetCount = planets?.length || 0;
  const stationSize = 12;

  stations.forEach((station, index) => {
    let stationX: number;
    let stationY: number;
    let shouldDrawOrbit = false;

    if (planetCount > 0 && index < planetCount) {
      // Station orbits a planet
      const spacing = width / (planetCount + 1);
      const orbitRadius = 60;
      const planetX = spacing * (index + 1);
      const planetY = height * 0.5;

      // Calculate orbital position using time
      const time = Date.now() * 0.0003;
      const orbitAngle = time + (index * Math.PI * 0.5);
      stationX = planetX + Math.cos(orbitAngle) * orbitRadius;
      stationY = planetY + Math.sin(orbitAngle) * orbitRadius;
      shouldDrawOrbit = true;

      // Draw orbital path (faint circle)
      ctx.strokeStyle = 'rgba(0, 217, 255, 0.15)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(planetX, planetY, orbitRadius, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      // Standalone station - position in viewport without planet
      const stationCount = stations.length - planetCount;
      const stationIndex = index - planetCount;
      const spacing = width / (stationCount + 1);
      stationX = spacing * (stationIndex + 1);
      stationY = height * 0.5;

      // Gentle floating animation for standalone stations
      const floatOffset = Math.sin(Date.now() * 0.001 + index) * 5;
      stationY += floatOffset;
    }

    // Track position for hit detection
    entityPositions.push({
      type: 'station',
      id: station.id,
      name: station.name,
      x: stationX,
      y: stationY,
      radius: stationSize + 8 // Hit area
    });

    // Station structure - hexagonal space station
    ctx.strokeStyle = '#00d9ff';
    ctx.fillStyle = 'rgba(0, 217, 255, 0.3)';
    ctx.lineWidth = 2;

    ctx.beginPath();
    for (let i = 0; i < 6; i++) {
      const angle = (Math.PI / 3) * i;
      const px = stationX + stationSize * Math.cos(angle);
      const py = stationY + stationSize * Math.sin(angle);
      if (i === 0) {
        ctx.moveTo(px, py);
      } else {
        ctx.lineTo(px, py);
      }
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Station core (inner circle)
    ctx.fillStyle = 'rgba(0, 217, 255, 0.6)';
    ctx.beginPath();
    ctx.arc(stationX, stationY, stationSize * 0.4, 0, Math.PI * 2);
    ctx.fill();

    // Blinking status light
    const blink = Math.sin(Date.now() * 0.003 + index) > 0.5;
    if (blink) {
      ctx.fillStyle = '#00ff41';
      ctx.beginPath();
      ctx.arc(stationX, stationY, 2, 0, Math.PI * 2);
      ctx.fill();
    }

    // Draw label
    ctx.font = '11px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';

    // Label background
    const labelY = stationY + stationSize + 8;
    const labelWidth = ctx.measureText(station.name).width + 8;
    ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
    ctx.fillRect(stationX - labelWidth / 2, labelY, labelWidth, 16);

    // Label text
    ctx.fillStyle = '#00d9ff';
    ctx.fillText(station.name, stationX, labelY + 2);
  });
}

function drawSectorEffects(
  ctx: CanvasRenderingContext2D,
  sectorType: string,
  width: number,
  height: number,
  hazardLevel: number,
  radiationLevel: number
) {
  // Normalize sector type
  const normalizedType = sectorType?.toLowerCase() || 'normal';

  // Radiation glow overlay
  if (radiationLevel > 0) {
    ctx.fillStyle = `rgba(0, 255, 65, ${radiationLevel * 0.1})`;
    ctx.fillRect(0, 0, width, height);
  }

  // Hazard warning pulse
  if (hazardLevel > 5) {
    const pulse = Math.sin(Date.now() * 0.005) * 0.5 + 0.5;
    ctx.strokeStyle = `rgba(255, 107, 0, ${pulse * 0.3})`;
    ctx.lineWidth = 3;
    ctx.strokeRect(2, 2, width - 4, height - 4);
  }

  // Sector-specific overlays
  if (normalizedType === 'void') {
    // Vignette effect for void sectors
    const gradient = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, width / 2);
    gradient.addColorStop(0, 'rgba(5, 8, 16, 0)');
    gradient.addColorStop(1, 'rgba(5, 8, 16, 0.8)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);
  }
}

export default SectorViewport;
