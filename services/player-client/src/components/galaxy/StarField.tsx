import { useRef, useMemo } from 'react';
import * as THREE from 'three';

interface StarFieldProps {
  count?: number;
  radius?: number;
  /** Place stars on a thin outer shell (backdrop sky) instead of filling
   *  the volume — avoids looking like thousands of sector nodes. */
  shell?: boolean;
}

/** Deterministic hash in [0,1) — no Math.random so remounts don't flicker. */
function hash01(n: number): number {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

export default function StarField({
  count = 90,
  radius = 120,
  shell = true,
}: StarFieldProps) {
  const pointsRef = useRef<THREE.Points>(null);

  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    const positions = new Float32Array(count * 3);
    const colors = new Float32Array(count * 3);

    for (let i = 0; i < count; i++) {
      const i3 = i * 3;
      const u = hash01(i * 3 + 1);
      const v = hash01(i * 3 + 2);
      const w = hash01(i * 3 + 3);

      const theta = u * Math.PI * 2;
      const phi = Math.acos(v * 2 - 1);
      const r = shell
        ? radius * (0.92 + w * 0.08)
        : w * radius;

      positions[i3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i3 + 2] = r * Math.cos(phi);

      if (u < 0.7) {
        colors[i3] = 0.85;
        colors[i3 + 1] = 0.88;
        colors[i3 + 2] = 0.95;
      } else if (u < 0.85) {
        colors[i3] = 0.55;
        colors[i3 + 1] = 0.65;
        colors[i3 + 2] = 1;
      } else {
        colors[i3] = 1;
        colors[i3 + 1] = 0.75;
        colors[i3 + 2] = 0.5;
      }
    }

    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));

    return geo;
  }, [count, radius, shell]);

  return (
    <points ref={pointsRef} geometry={geometry}>
      <pointsMaterial
        transparent
        vertexColors
        size={0.35}
        sizeAttenuation={true}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        opacity={0.55}
      />
    </points>
  );
}
