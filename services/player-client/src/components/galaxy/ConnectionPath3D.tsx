import { useMemo } from 'react';
import { Line } from '@react-three/drei';
import { Vector3 } from 'three';

interface ConnectionPath3DProps {
  start: Vector3;
  end: Vector3;
  type: 'warp' | 'tunnel';
  lodLevel: {
    detail: 'high' | 'medium' | 'low';
    showLabels: boolean;
    showEffects: boolean;
  };
}

export default function ConnectionPath3D({ start, end, type, lodLevel }: ConnectionPath3DProps) {
  // Stable curve — no Math.random (refresh/remount must not reshuffle paths).
  const points = useMemo(() => {
    if (lodLevel.detail === 'low') {
      return [start, end];
    }

    const midPoint = start.clone().lerp(end, 0.5);
    const dir = end.clone().sub(start);
    const distance = dir.length();
    if (distance < 1e-6) {
      return [start, end];
    }

    const up = Math.abs(dir.y / distance) < 0.9
      ? new Vector3(0, 1, 0)
      : new Vector3(1, 0, 0);
    const perp = new Vector3().crossVectors(dir, up).normalize();
    const bulge = type === 'tunnel' ? 0.16 : 0.1;
    midPoint.add(perp.multiplyScalar(distance * bulge));
    midPoint.y += distance * 0.06;

    return [start, midPoint, end];
  }, [start, end, lodLevel.detail, type]);

  const color = type === 'tunnel' ? '#ff4444' : '#4488ff';
  const lineWidth = type === 'tunnel' ? 3 : 1;

  if (lodLevel.detail === 'low' && start.distanceTo(end) > 50) {
    return null;
  }

  return (
    <Line
      points={points}
      color={color}
      lineWidth={lineWidth}
      transparent
      opacity={0.6}
    />
  );
}
