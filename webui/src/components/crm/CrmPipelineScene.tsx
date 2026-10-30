// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useEffect, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, OrbitControls } from "@react-three/drei";
import type { Group } from "three";

import { StudioEnvironment } from "@/components/studio/StudioEnvironment";
import { SceneBoundary } from "@/components/three/SceneBoundary";
import { usePageVisible } from "@/hooks/usePageVisible";

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

/**
 * Animates only while on screen: it used to draw 60 frames a second for as
 * long as the CRM stayed open, scrolled away or not, like no other scene.
 */
export function CrmPipelineScene() {
  const host = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(true);
  const pageVisible = usePageVisible();
  const reduced =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const playing = inView && pageVisible && !reduced;

  useEffect(() => {
    const el = host.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const obs = new IntersectionObserver(
      ([entry]) => setInView(Boolean(entry?.isIntersecting)),
      { threshold: 0.15 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <div ref={host} className="h-36 w-full overflow-hidden rounded-xl bg-muted/30">
      <SceneBoundary>
        <Canvas
          role="img"
          aria-label="Pipeline commercial en volume"
          shadows
          frameloop={playing ? "always" : "demand"}
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
            <PipelineRings reduced={!playing} />
            <StudioEnvironment />
            <ContactShadows position={[0, -0.55, 0]} opacity={0.4} scale={6} blur={2.2} />
          </Suspense>
          <OrbitControls enablePan={false} enableZoom={false} dampingFactor={0.12} />
        </Canvas>
      </SceneBoundary>
    </div>
  );
}
