// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, OrbitControls } from "@react-three/drei";
import type { Group, Mesh } from "three";
import { StudioEnvironment } from "@/components/studio/StudioEnvironment";

function Orbit({
  heat,
  reduced,
}: {
  heat: number;
  reduced: boolean;
}) {
  const group = useRef<Group>(null);
  const ring = useRef<Mesh>(null);
  const inner = useRef<Mesh>(null);
  const accent = heat >= 0.5 ? "#22c55e" : heat > 0 ? "#a855f7" : "#64748b";
  useFrame((_, delta) => {
    if (reduced) return;
    if (group.current) group.current.rotation.y += delta * 0.22;
    if (ring.current) ring.current.rotation.z -= delta * 0.16;
    if (inner.current) inner.current.rotation.x += delta * 0.1;
  });
  return (
    <group ref={group} position={[0, 0.06, 0]}>
      <mesh castShadow>
        <icosahedronGeometry args={[0.28, 1]} />
        <meshStandardMaterial color="#0f172a" metalness={0.86} roughness={0.18} />
      </mesh>
      <mesh ref={inner} rotation={[0.4, 0.2, 0]}>
        <torusGeometry args={[0.52, 0.045, 20, 64]} />
        <meshStandardMaterial color={accent} metalness={0.4} roughness={0.28} emissive={accent} emissiveIntensity={0.22} />
      </mesh>
      <mesh ref={ring} rotation={[Math.PI / 2.4, 0.15, 0]}>
        <torusGeometry args={[0.92, 0.018, 12, 80]} />
        <meshStandardMaterial color="#e2e8f0" metalness={0.7} roughness={0.2} />
      </mesh>
      <mesh position={[0.78, 0.22, 0.12]} castShadow>
        <sphereGeometry args={[0.07, 24, 24]} />
        <meshStandardMaterial color="#f59e0b" metalness={0.5} roughness={0.3} />
      </mesh>
    </group>
  );
}

export function MarketingScene({
  heat = 0,
  active = true,
  label,
}: {
  heat?: number;
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
  const clamped = useMemo(() => Math.max(0, Math.min(1, heat)), [heat]);

  useEffect(() => {
    const onVis = () => setPageVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  useEffect(() => {
    const el = host.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver(
      ([entry]) => setInView(Boolean(entry?.isIntersecting)),
      { threshold: 0.12 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <div
      ref={host}
      className="h-64 w-full overflow-hidden rounded-[14px] bg-[#0f172a] shadow-[0_1px_0_rgba(255,255,255,0.04),0_12px_28px_rgba(15,23,42,0.18)] outline outline-1 outline-black/10 dark:outline-white/10"
    >
      <Canvas
        role="img"
        aria-label={label}
        shadows
        frameloop={playing ? "always" : "demand"}
        camera={{ position: [0, 0.68, 2.5], fov: 45 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
      >
        <color attach="background" args={["#0f172a"]} />
        <ambientLight intensity={0.4} />
        <directionalLight position={[2.1, 3.2, 2.2]} intensity={1.25} castShadow />
        <Suspense fallback={null}>
          <Orbit heat={clamped} reduced={!playing} />
          <StudioEnvironment />
          <ContactShadows position={[0, -0.7, 0]} opacity={0.42} scale={5.2} blur={2.2} />
        </Suspense>
        <OrbitControls enablePan={false} enableZoom={false} enableDamping dampingFactor={0.12} />
      </Canvas>
    </div>
  );
}
