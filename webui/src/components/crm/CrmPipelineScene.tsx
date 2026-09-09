// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Environment, OrbitControls } from "@react-three/drei";
import type { Group } from "three";

const STAGES = [
  { x: -1.6, color: "#7c3aed" },
  { x: -0.8, color: "#6366f1" },
  { x: 0, color: "#0ea5e9" },
  { x: 0.8, color: "#14b8a6" },
  { x: 1.6, color: "#22c55e" },
];

function PipelineRings({ reduced }: { reduced: boolean }) {
  const group = useRef<Group>(null);
  useFrame((_, delta) => {
    if (reduced || !group.current) return;
    group.current.rotation.y += delta * 0.35;
  });
  return (
    <group ref={group} position={[0, 0.15, 0]}>
      {STAGES.map((stage, index) => (
        <mesh key={stage.x} position={[stage.x, index * 0.04, 0]} castShadow receiveShadow>
          <cylinderGeometry args={[0.28 - index * 0.02, 0.32 - index * 0.02, 0.18, 32]} />
          <meshStandardMaterial
            color={stage.color}
            metalness={0.35}
            roughness={0.28}
            emissive={stage.color}
            emissiveIntensity={0.12}
          />
        </mesh>
      ))}
    </group>
  );
}

export function CrmPipelineScene() {
  const reduced =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return (
    <div className="h-36 w-full overflow-hidden rounded-xl bg-muted/30">
      <Canvas
        role="img"
        aria-label="Pipeline commercial en volume"
        shadows
        camera={{ position: [0, 1.4, 3.2], fov: 50 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
      >
        <ambientLight intensity={0.55} />
        <directionalLight
          position={[3, 5, 2]}
          intensity={1.1}
          castShadow
          shadow-mapSize-width={1024}
          shadow-mapSize-height={1024}
        />
        <Suspense fallback={null}>
          <PipelineRings reduced={reduced} />
          <Environment preset="city" />
          <ContactShadows position={[0, -0.55, 0]} opacity={0.4} scale={6} blur={2.2} />
        </Suspense>
        <OrbitControls enablePan={false} enableZoom={false} dampingFactor={0.12} />
      </Canvas>
    </div>
  );
}
