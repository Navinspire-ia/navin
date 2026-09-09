// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, OrbitControls } from "@react-three/drei";
import type { Group } from "three";
import { StudioEnvironment } from "@/components/studio/StudioEnvironment";

function Briefcase({ heat, reduced }: { heat: number; reduced: boolean }) {
  const group = useRef<Group>(null);
  useFrame((_, delta) => {
    if (reduced) return;
    if (group.current) group.current.rotation.y += delta * 0.18;
  });
  const metal = heat >= 0.7 ? "#4F46E5" : heat >= 0.4 ? "#2563EB" : "#64748B";
  return (
    <group ref={group} position={[0, 0.08, 0]}>
      <mesh castShadow position={[0, 0.02, 0]}>
        <boxGeometry args={[1.15, 0.72, 0.38]} />
        <meshStandardMaterial color="#1E293B" metalness={0.25} roughness={0.42} />
      </mesh>
      <mesh castShadow position={[0, 0.42, 0]}>
        <torusGeometry args={[0.28, 0.035, 16, 48, Math.PI]} />
        <meshStandardMaterial color={metal} metalness={0.7} roughness={0.2} />
      </mesh>
      <mesh position={[0, 0.04, 0.2]}>
        <boxGeometry args={[0.22, 0.08, 0.04]} />
        <meshStandardMaterial color={metal} metalness={0.65} roughness={0.22} emissive={metal} emissiveIntensity={0.2} />
      </mesh>
      <mesh position={[-0.38, -0.02, 0.2]} rotation={[0.15, 0.1, 0.05]}>
        <boxGeometry args={[0.28, 0.36, 0.02]} />
        <meshStandardMaterial color="#F8FAFC" metalness={0.05} roughness={0.45} />
      </mesh>
      <mesh position={[0.4, 0.06, 0.18]} rotation={[-0.2, -0.12, 0.08]}>
        <boxGeometry args={[0.26, 0.34, 0.02]} />
        <meshStandardMaterial color="#EEF2FF" metalness={0.08} roughness={0.4} />
      </mesh>
    </group>
  );
}

export function CareerScene({
  matchRatio = 0,
  active = true,
  label,
}: {
  matchRatio?: number;
  active?: boolean;
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(true);
  const [pageVisible, setPageVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible",
  );
  const reduced =
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const playing = Boolean(active && inView && pageVisible && !reduced);
  const heat = useMemo(() => Math.max(0, Math.min(1, matchRatio)), [matchRatio]);

  useEffect(() => {
    const onVis = () => setPageVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  useEffect(() => {
    const el = host.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const obs = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { threshold: 0.15 });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <div ref={host} className="h-36 w-full overflow-hidden rounded-2xl" role="img" aria-label={label}>
      <Suspense fallback={<div className="h-full w-full bg-muted/40" />}>
        <Canvas
          shadows
          dpr={[1, 2]}
          camera={{ position: [1.7, 1.15, 2.15], fov: 45 }}
          frameloop={playing ? "always" : "demand"}
          gl={{ antialias: true }}
        >
          <ambientLight intensity={0.55} />
          <directionalLight
            position={[2.2, 3.1, 1.7]}
            intensity={1.15}
            castShadow
            shadow-mapSize-width={1024}
            shadow-mapSize-height={1024}
          />
          <Briefcase heat={heat} reduced={!playing} />
          <ContactShadows position={[0, -0.55, 0]} opacity={0.35} scale={4} blur={2.2} />
          <StudioEnvironment />
          <OrbitControls enablePan={false} enableZoom={false} enableDamping dampingFactor={0.08} />
        </Canvas>
      </Suspense>
    </div>
  );
}
