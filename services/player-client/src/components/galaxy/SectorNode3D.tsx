import { useRef, useState, useMemo } from 'react';
import { useFrame } from '@react-three/fiber';
import { Text, Sphere, Box, Cylinder } from '@react-three/drei';
import { Color } from 'three';
import * as THREE from 'three';

import { Sector } from '../../contexts/GameContext';

interface SectorNode3DProps {
  sector: Sector;
  position: THREE.Vector3;
  isSelected: boolean;
  isCurrent: boolean;
  onClick: (sector: Sector) => void;
  lodLevel: {
    detail: 'high' | 'medium' | 'low';
    showLabels: boolean;
    showEffects: boolean;
  };
  playerCount: number;
}

export default function SectorNode3D({
  sector,
  position,
  isSelected,
  isCurrent,
  onClick,
  lodLevel,
  playerCount
}: SectorNode3DProps) {
  const groupRef = useRef<THREE.Group>(null);
  const meshRef = useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = useState(false);

  // Sector type visual configuration
  const sectorConfig = useMemo(() => {
    const configs = {
      'normal': {
        color: '#4488ff',
        emissive: '#001122',
        geometry: 'sphere',
        scale: 1.0,
        glow: false
      },
      'nebula': {
        color: '#ff6644',
        emissive: '#220011',
        geometry: 'sphere',
        scale: 1.2,
        glow: true
      },
      'asteroid': {
        color: '#888844',
        emissive: '#111100',
        geometry: 'box',
        scale: 0.8,
        glow: false
      },
      'blackhole': {
        color: '#220022',
        emissive: '#440044',
        geometry: 'sphere',
        scale: 1.5,
        glow: true
      },
      'star': {
        color: '#ffff44',
        emissive: '#444400',
        geometry: 'sphere',
        scale: 1.3,
        glow: true
      },
      'wormhole': {
        color: '#4444ff',
        emissive: '#000044',
        geometry: 'cylinder',
        scale: 1.0,
        glow: true
      }
    };

    return configs[sector.type as keyof typeof configs] || configs.normal;
  }, [sector.type]);

  // Activity-based color intensity
  const activityIntensity = useMemo(() => {
    if (playerCount === 0) return 0.3;
    if (playerCount <= 2) return 0.6;
    if (playerCount <= 5) return 0.8;
    return 1.0;
  }, [playerCount]);

  // Target values for smooth animation (lerped in useFrame)
  const targetScale = isSelected ? 1.3 : isCurrent ? 1.1 : hovered ? 1.05 : 1.0;
  const targetOpacity = lodLevel.detail === 'low' ? 0.7 : 1.0;

  // Continuous animation via useFrame (replaces react-spring)
  useFrame((state) => {
    // Smooth scale animation
    if (groupRef.current) {
      const currentScale = groupRef.current.scale.x;
      const newScale = THREE.MathUtils.lerp(currentScale, targetScale, 0.1);
      groupRef.current.scale.setScalar(newScale);
    }

    // Continuous rotation for active sectors
    if (meshRef.current && (isCurrent || playerCount > 0)) {
      meshRef.current.rotation.x += 0.01;
      meshRef.current.rotation.z += 0.005;
    }

    // Glow effect for special sector types
    if (meshRef.current && sectorConfig.glow && lodLevel.showEffects) {
      const time = state.clock.getElapsedTime();
      const intensity = 0.5 + Math.sin(time * 2) * 0.3;
      (meshRef.current.material as THREE.MeshStandardMaterial).emissiveIntensity = intensity;
    }

    // Update opacity on material
    if (meshRef.current) {
      const mat = meshRef.current.material as THREE.MeshStandardMaterial;
      if (mat && mat.opacity !== undefined) {
        mat.opacity = THREE.MathUtils.lerp(mat.opacity, targetOpacity, 0.1);
      }
    }
  });

  // Handle click events
  const handleClick = (event: any) => {
    event.stopPropagation();
    onClick(sector);
  };

  // Handle hover events
  const handlePointerOver = (event: any) => {
    event.stopPropagation();
    setHovered(true);
    document.body.style.cursor = 'pointer';
  };

  const handlePointerOut = () => {
    setHovered(false);
    document.body.style.cursor = 'auto';
  };

  // Color calculation based on state and activity
  const finalColor = useMemo(() => {
    const baseColor = new Color(sectorConfig.color);

    if (isCurrent) {
      return baseColor.clone().lerp(new Color('#00ff00'), 0.3);
    } else if (isSelected) {
      return baseColor.clone().lerp(new Color('#ffff00'), 0.3);
    } else if (hovered) {
      return baseColor.clone().lerp(new Color('#ffffff'), 0.2);
    }

    // Adjust based on activity
    return baseColor.clone().multiplyScalar(0.5 + activityIntensity * 0.5);
  }, [sectorConfig.color, isCurrent, isSelected, hovered, activityIntensity]);

  const emissiveColor = useMemo(() => {
    const baseEmissive = new Color(sectorConfig.emissive);
    return baseEmissive.clone().multiplyScalar(activityIntensity);
  }, [sectorConfig.emissive, activityIntensity]);

  // Geometry based on sector type and LOD
  const renderGeometry = () => {
    const size = sectorConfig.scale * (lodLevel.detail === 'low' ? 0.5 : 1.0);

    const material = (
      <meshStandardMaterial
        color={finalColor}
        emissive={emissiveColor}
        emissiveIntensity={sectorConfig.glow ? 0.3 : 0.1}
        metalness={0.3}
        roughness={0.7}
        transparent={true}
        opacity={targetOpacity}
      />
    );

    switch (sectorConfig.geometry) {
      case 'box':
        return (
          <Box args={[size * 2, size * 2, size * 2]}>
            {material}
          </Box>
        );
      case 'cylinder':
        return (
          <Cylinder args={[size, size, size * 3, 16]}>
            {material}
          </Cylinder>
        );
      default:
        return (
          <Sphere args={[size, lodLevel.detail === 'low' ? 8 : 16, lodLevel.detail === 'low' ? 6 : 12]}>
            {material}
          </Sphere>
        );
    }
  };

  // Player count indicator
  const renderPlayerIndicator = () => {
    if (playerCount === 0 || !lodLevel.showEffects) return null;

    return (
      <group position={[0, sectorConfig.scale + 1, 0]}>
        <Sphere args={[0.2, 8, 6]}>
          <meshBasicMaterial color="#00ff00" />
        </Sphere>
        {lodLevel.showLabels && (
          <Text
            position={[0, 0.5, 0]}
            fontSize={0.3}
            color="#ffffff"
            anchorX="center"
            anchorY="middle"
          >
            {playerCount}
          </Text>
        )}
      </group>
    );
  };

  // Sector label
  const renderLabel = () => {
    if (!lodLevel.showLabels || lodLevel.detail === 'low') return null;

    return (
      <Text
        position={[0, -sectorConfig.scale - 1, 0]}
        fontSize={0.4}
        color={isCurrent ? "#00ff00" : isSelected ? "#ffff00" : "#ffffff"}
        anchorX="center"
        anchorY="middle"
        maxWidth={8}
        textAlign="center"
      >
        {sector.name}
      </Text>
    );
  };

  // Special-formation anomaly markers (WO-SFM). Keyed on the discovery-aware
  // `special_formations` field (NOT the legacy `special_features` string array,
  // which the 3D map never populated). A DISCOVERED formation shows a steady
  // amber glyph plus its NAME · TYPE label; an UNDISCOVERED one shows a dimmer,
  // identity-less cyan glyph labelled only "ANOMALY" — name/type are withheld
  // by the server until the sector is visited (mirrors the WO-CA HUD chip rule),
  // so the map never leaks a formation's identity before discovery.
  const renderFeatureIndicators = () => {
    if (!lodLevel.showEffects || lodLevel.detail === 'low') return null;

    const formations = sector.special_formations;
    if (!formations || formations.length === 0) return null;

    const anyDiscovered = formations.some(f => f.is_discovered);
    const discovered = formations.filter(f => f.is_discovered);

    // Build a concise label: discovered formations by NAME · TYPE, else a
    // generic count of unknown anomalies (no identity).
    const label = anyDiscovered
      ? discovered
          .map(f =>
            `${(f.name || 'UNNAMED').toUpperCase()}${f.type ? ` · ${f.type.replace(/_/g, ' ').toUpperCase()}` : ''}`
          )
          .join('\n')
      : formations.length > 1
        ? `${formations.length} ANOMALIES`
        : 'ANOMALY';

    // Discovered markers glow steady amber; undiscovered ones use a dimmer cyan
    // so an unidentified anomaly reads as "there is *something* here" without
    // revealing what.
    const glyphColor = anyDiscovered ? '#ffaa00' : '#33ccdd';

    return [
      <group key="formations" position={[sectorConfig.scale + 0.6, 0, 0]}>
        <mesh>
          <octahedronGeometry args={[0.28, 0]} />
          <meshBasicMaterial
            color={glyphColor}
            transparent
            opacity={anyDiscovered ? 0.95 : 0.6}
          />
        </mesh>
        {lodLevel.showLabels && (
          <Text
            position={[0, 0.55, 0]}
            fontSize={0.28}
            color={glyphColor}
            anchorX="center"
            anchorY="bottom"
            maxWidth={6}
            textAlign="center"
            outlineWidth={0.02}
            outlineColor="#000000"
          >
            {label}
          </Text>
        )}
      </group>
    ];
  };

  return (
    <group
      ref={groupRef}
      position={position.toArray()}
      onClick={handleClick}
      onPointerOver={handlePointerOver}
      onPointerOut={handlePointerOut}
    >
      {/* Main sector geometry */}
      <mesh ref={meshRef}>
        {renderGeometry()}
      </mesh>

      {/* Player count indicator */}
      {renderPlayerIndicator()}

      {/* Sector label */}
      {renderLabel()}

      {/* Feature indicators */}
      {renderFeatureIndicators()}

      {/* Selection ring */}
      {(isSelected || isCurrent) && lodLevel.showEffects && (
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <ringGeometry args={[sectorConfig.scale + 0.5, sectorConfig.scale + 0.7, 32]} />
          <meshBasicMaterial
            color={isCurrent ? "#00ff00" : "#ffff00"}
            transparent
            opacity={0.6}
            side={THREE.DoubleSide}
          />
        </mesh>
      )}
    </group>
  );
}
