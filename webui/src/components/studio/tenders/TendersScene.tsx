// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, OrbitControls } from "@react-three/drei";
import type { Group } from "three";
import { StudioEnvironment } from "@/components/studio/StudioEnvironment";

function Dossier({ heat, reduced }: { heat: number; reduced: boolean }) {
  const group = useRef<Group>(null);
  useFrame((_, delta) => {
    if (reduced) return;
    if (group.current) group.current.rotation.y += delta * 0.16;
  });
  const seal = heat >= 0.6 ? "#15803D" : heat >= 0.35 ? "#166534" : "#64748B";
  return (
    <group ref={group} position={[0, 0.06, 0]}>
      <mesh castShadow position={[0, -0.02, 0.02]} rotation={[-0.12, 0.08, 0]}>
        <boxGeometry args={[1.15, 0.08, 0.82]} />
        <meshStandardMaterial color="#E8E4D9" metalness={0.05} roughness={0.55} />
      </mesh>
      <mesh castShadow position={[0, 0.07, 0]} rotation={[-0.04, -0.04, 0]}>
        <boxGeometry args={[1.08, 0.07, 0.76]} />
        <meshStandardMaterial color="#F4F1EA" metalness={0.04} roughness={0.48} />
      </mesh>
      <mesh castShadow position={[0, 0.16, -0.02]} rotation={[0.08, 0.05, 0]}>
        <boxGeometry args={[1.02, 0.06, 0.7]} />
        <meshStandardMaterial color="#FAFAF7" metalness={0.08} roughness={0.4} />
      </mesh>
      <mesh position={[0.28, 0.22, 0.12]} rotation={[Math.PI / 2, 0, 0]}>
        <cylinderGeometry args={[0.09, 0.09, 0.02, 32]} />
        <meshStandardMaterial color={seal} metalness={0.55} roughness={0.25} emissive={seal} emissiveIntensity={0.18} />
      </mesh>
      <mesh rotation={[0.4, 0.3, 0.1]}>
        <torusGeometry args={[0.92, 0.012, 12, 48]} />
        <meshStandardMaterial color="#94A3B8" metalness={0.7} roughness={0.22} />
      </mesh>
    </group>
  );
}

export function TendersScene({
  qualifiedRatio = 0,
  active = true,
  label,
  className,
}: {
  qualifiedRatio?: number;
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
  const heat = useMemo(() => Math.max(0, Math.min(1, qualifiedRatio)), [qualifiedRatio]);

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
    <div
      ref={host}
      className={["h-44 w-full overflow-hidden rounded-2xl", className].filter(Boolean).join(" ")}
      role="img"
      aria-label={label}
    >
      <Suspense fallback={<div className="h-full w-full bg-muted/40" />}>
        <Canvas
          shadows
          dpr={[1, 2]}
          camera={{ position: [1.6, 1.1, 2.2], fov: 45 }}
          frameloop={playing ? "always" : "demand"}
          gl={{ antialias: true }}
        >
          <ambientLight intensity={0.55} />
          <directionalLight
            position={[2.4, 3.2, 1.6]}
            intensity={1.15}
            castShadow
            shadow-mapSize-width={1024}
            shadow-mapSize-height={1024}
          />
          <Dossier heat={heat} reduced={!playing} />
          <ContactShadows position={[0, -0.55, 0]} opacity={0.35} scale={4} blur={2.2} />
          <StudioEnvironment />
          <OrbitControls enablePan={false} enableZoom={false} enableDamping dampingFactor={0.08} />
        </Canvas>
      </Suspense>
    </div>
  );
}
