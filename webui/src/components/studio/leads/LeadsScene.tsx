// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Environment, OrbitControls } from "@react-three/drei";
import type { Group } from "three";

import { cn } from "@/lib/utils";

function Funnel({ heat, reduced }: { heat: number; reduced: boolean }) {
  const group = useRef<Group>(null);
  useFrame((_, delta) => {
    if (reduced) return;
    if (group.current) group.current.rotation.y += delta * 0.16;
  });
  const glow = heat >= 0.7 ? "#4F46E5" : heat >= 0.35 ? "#2563EB" : "#64748B";
  return (
    <group ref={group} position={[0, 0.06, 0]}>
      <mesh castShadow position={[0, 0.42, 0]}>
        <cylinderGeometry args={[0.72, 0.58, 0.22, 48]} />
        <meshStandardMaterial color="#1E293B" metalness={0.28} roughness={0.4} />
      </mesh>
      <mesh castShadow position={[0, 0.12, 0]}>
        <cylinderGeometry args={[0.5, 0.28, 0.42, 48]} />
        <meshStandardMaterial color={glow} metalness={0.55} roughness={0.22} emissive={glow} emissiveIntensity={0.18} />
      </mesh>
      <mesh castShadow position={[0, -0.28, 0]}>
        <cylinderGeometry args={[0.16, 0.22, 0.28, 32]} />
        <meshStandardMaterial color="#0F172A" metalness={0.35} roughness={0.35} />
      </mesh>
      <mesh position={[0.55, 0.38, 0.2]}>
        <sphereGeometry args={[0.08, 24, 24]} />
        <meshStandardMaterial color="#C7D2FE" metalness={0.4} roughness={0.25} />
      </mesh>
      <mesh position={[-0.48, 0.22, 0.28]}>
        <sphereGeometry args={[0.06, 24, 24]} />
        <meshStandardMaterial color="#F8FAFC" metalness={0.2} roughness={0.35} />
      </mesh>
    </group>
  );
}

export function LeadsScene({
  matchRatio = 0,
  active = true,
  label,
  className,
}: {
  matchRatio?: number;
  active?: boolean;
  label: string;
  className?: string;
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
    <div ref={host} className={cn("h-36 w-full overflow-hidden rounded-2xl", className)} role="img" aria-label={label}>
      <Suspense fallback={<div className="h-full w-full bg-muted/40" />}>
        <Canvas
          shadows
          dpr={[1, 2]}
          camera={{ position: [1.7, 1.2, 2.2], fov: 42 }}
          frameloop={playing ? "always" : "demand"}
          gl={{ antialias: true }}
        >
          <ambientLight intensity={0.55} />
          <directionalLight position={[2.4, 3.2, 1.6]} intensity={1.15} castShadow />
          <Funnel heat={heat} reduced={!playing} />
          <Environment preset="city" />
          <ContactShadows position={[0, -0.55, 0]} opacity={0.35} blur={2.2} scale={6} />
          <OrbitControls enablePan={false} enableZoom={false} dampingFactor={0.12} />
        </Canvas>
      </Suspense>
    </div>
  );
}
