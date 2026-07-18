import { useRef } from 'react';
import { Text, Cone } from '@react-three/drei';
import { Vector3 } from 'three';
import * as THREE from 'three';

interface PlayerMarker3DProps {
  player: {
    user_id: string;
    username: string;
    ship_type?: string;
  };
  position: Vector3;
  lodLevel: {
    detail: 'high' | 'medium' | 'low';
    showLabels: boolean;
    showEffects: boolean;
  };
}

export default function PlayerMarker3D({ player, position, lodLevel }: PlayerMarker3DProps) {
  const markerRef = useRef<THREE.Group>(null);

  if (lodLevel.detail === 'low') return null;

  return (
    <group ref={markerRef} position={position.toArray()}>
      {/* Player ship representation */}
      <Cone args={[0.3, 1, 6]} rotation={[0, 0, Math.PI]}>
        <meshBasicMaterial color="#00ff88" />
      </Cone>
      
      {/* Player name label */}
      {lodLevel.showLabels && (
        <Text
          position={[0, 1.5, 0]}
          fontSize={0.3}
          color="#00ff88"
          anchorX="center"
          anchorY="middle"
        >
          {player.username}
        </Text>
      )}
    </group>
  );
}