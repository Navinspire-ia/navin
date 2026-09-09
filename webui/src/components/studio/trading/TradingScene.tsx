// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Environment, OrbitControls } from "@react-three/drei";
import type { Group, Mesh } from "three";

function Core({
  heat,
  reduced,
}: {
  heat: number;
  reduced: boolean;
}) {
  const group = useRef<Group>(null);
  const yellow = useRef<Mesh>(null);
  const silver = useRef<Mesh>(null);
  const gold = useRef<Mesh>(null);
  const yellowColor = heat <= -0.04 ? "#dc2626" : "#F5C518";
  useFrame((_, delta) => {
    if (reduced) return;
    if (group.current) group.current.rotation.y += delta * 0.18;
    if (yellow.current) yellow.current.rotation.y += delta * 0.12;
    if (silver.current) silver.current.rotation.z -= delta * 0.2;
    if (gold.current) gold.current.rotation.x += delta * 0.08;
  });
  return (
    <group ref={group} position={[0, 0.08, 0]}>
      <mesh castShadow>
        <sphereGeometry args={[0.22, 48, 48]} />
        <meshStandardMaterial color="#0a0a0a" metalness={0.92} roughness={0.12} />
      </mesh>
      <mesh ref={yellow} rotation={[0, Math.PI / 2, 0]} castShadow>
        <torusGeometry args={[0.58, 0.155, 36, 80]} />
        <meshStandardMaterial
          color={yellowColor}
          metalness={0.35}
          roughness={0.28}
          emissive={yellowColor}
          emissiveIntensity={0.08}
        />
      </mesh>
      <mesh ref={silver} rotation={[Math.PI / 2, 0.08, 0]} castShadow>
        <torusGeometry args={[0.82, 0.032, 16, 80]} />
        <meshStandardMaterial color="#d7dce3" metalness={0.72} roughness={0.22} />
      </mesh>
      <mesh ref={gold} rotation={[0.62, 0.22, 0.18]}>
        <torusGeometry args={[1.18, 0.012, 12, 96]} />
        <meshStandardMaterial color="#E8C547" metalness={0.82} roughness={0.18} emissive="#E8C547" emissiveIntensity={0.16} />
      </mesh>
    </group>
  );
}

export function TradingScene({
  returnPct = 0,
  active = true,
  label,
}: {
  returnPct?: number;
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
  const heat = useMemo(() => Math.max(-1, Math.min(1, returnPct / 12)), [returnPct]);

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
      className="h-64 w-full overflow-hidden rounded-[14px] bg-[#161616] shadow-[0_1px_0_rgba(255,255,255,0.04),0_12px_28px_rgba(15,23,42,0.18)] outline outline-1 outline-black/10 dark:outline-white/10"
    >
      <Canvas
        role="img"
        aria-label={label}
        shadows
        frameloop={playing ? "always" : "demand"}
        camera={{ position: [0, 0.72, 2.55], fov: 42 }}
        dpr={[1, 2]}
        gl={{ antialias: true }}
      >
        <color attach="background" args={["#161616"]} />
        <ambientLight intensity={0.42} />
        <directionalLight
          position={[2.2, 3.4, 2.4]}
          intensity={1.35}
          castShadow
          shadow-mapSize-width={1024}
          shadow-mapSize-height={1024}
        />
        <Suspense fallback={null}>
          <Core heat={heat} reduced={!playing} />
          <Environment preset="studio" />
          <ContactShadows position={[0, -0.72, 0]} opacity={0.46} scale={5.4} blur={2.4} />
        </Suspense>
        <OrbitControls enablePan={false} enableZoom={false} enableDamping dampingFactor={0.12} />
      </Canvas>
    </div>
  );
}
