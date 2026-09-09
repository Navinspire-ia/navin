import { useEffect, useMemo, useRef, useState } from "react";
import { ContactShadows, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import type { Group } from "three";

import type { MontageTimeline } from "@/lib/api";
import { StudioEnvironment } from "@/components/studio/StudioEnvironment";
import { timelineDuration, visualDuration } from "./timelineModel";

type SpatialPreviewProps = {
  timeline: MontageTimeline;
  selectedIndex: number | null;
  label: string;
};

function TrackScene({
  timeline,
  selectedIndex,
  animate,
}: Omit<SpatialPreviewProps, "label"> & { animate: boolean }) {
  const group = useRef<Group>(null);
  const total = Math.max(1, timelineDuration(timeline));
  const clips = useMemo(() => {
    let cursor = -3.4;
    return timeline.visuals.map((visual, index) => {
      const width = Math.max(0.25, (visualDuration(visual) / total) * 6.8);
      const result = { visual, index, width, x: cursor + width / 2 };
      cursor += width + 0.06;
      return result;
    });
  }, [timeline, total]);

  useFrame(({ clock }) => {
    if (!animate || !group.current) return;
    group.current.rotation.y = Math.sin(clock.elapsedTime * 0.25) * 0.08;
  });

  return (
    <>
      <ambientLight intensity={0.65} />
      <directionalLight
        position={[4, 7, 5]}
        intensity={2.2}
        castShadow
        shadow-mapSize-width={1024}
        shadow-mapSize-height={1024}
      />
      <group ref={group} rotation={[-0.34, -0.08, 0]}>
        {clips.map(({ visual, index, width, x }) => (
          <mesh
            key={`${visual.path}-${index}`}
            position={[x, 0.45, 0]}
            castShadow
            receiveShadow
          >
            <boxGeometry args={[width, 0.34, 0.72]} />
            <meshStandardMaterial
              color={
                selectedIndex === index
                  ? "#EC4899"
                  : visual.kind === "video"
                    ? "#2563EB"
                    : "#38BDF8"
              }
              metalness={0.38}
              roughness={0.32}
            />
          </mesh>
        ))}
        {timeline.voice ? (
          <mesh position={[0, -0.12, 0.05]} castShadow>
            <boxGeometry args={[6.8, 0.16, 0.48]} />
            <meshStandardMaterial color="#A78BFA" metalness={0.2} roughness={0.44} />
          </mesh>
        ) : null}
        {timeline.music ? (
          <mesh position={[0, -0.48, 0.1]} castShadow>
            <boxGeometry args={[6.8, 0.16, 0.48]} />
            <meshStandardMaterial color="#34D399" metalness={0.2} roughness={0.4} />
          </mesh>
        ) : null}
        {timeline.subtitles ? (
          <mesh position={[0, -0.84, 0.15]} castShadow>
            <boxGeometry args={[6.8, 0.12, 0.4]} />
            <meshStandardMaterial color="#FBBF24" metalness={0.16} roughness={0.5} />
          </mesh>
        ) : null}
      </group>
      <ContactShadows
        position={[0, -1.15, 0]}
        opacity={0.55}
        scale={10}
        blur={2.4}
        far={4}
      />
      <StudioEnvironment />
      <OrbitControls
        enablePan={false}
        enableZoom
        minDistance={5.5}
        maxDistance={10}
        minPolarAngle={Math.PI / 4}
        maxPolarAngle={Math.PI / 2.25}
        dampingFactor={0.08}
        enableDamping
      />
    </>
  );
}

export function TimelineSpatialPreview({
  timeline,
  selectedIndex,
  label,
}: SpatialPreviewProps) {
  const reducedMotion = useReducedMotion();
  const [visible, setVisible] = useState(
    typeof document === "undefined" || document.visibilityState === "visible",
  );

  useEffect(() => {
    const onVisibility = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const animate = !reducedMotion && visible;
  return (
    <div className="h-36 min-h-36 overflow-hidden rounded-xl bg-[#080d19]" role="img" aria-label={label}>
      <Canvas
        shadows
        dpr={[1, 2]}
        frameloop={animate ? "always" : "demand"}
        camera={{ fov: 52, position: [0, 3.8, 7.2], near: 0.1, far: 100 }}
        gl={{ antialias: true, powerPreference: "high-performance" }}
      >
        <TrackScene timeline={timeline} selectedIndex={selectedIndex} animate={animate} />
      </Canvas>
    </div>
  );
}
