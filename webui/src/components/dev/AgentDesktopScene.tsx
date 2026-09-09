import { useEffect, useMemo, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { ContactShadows, Environment, Lightformer, OrbitControls } from "@react-three/drei";
import { CanvasTexture, SRGBColorSpace } from "three";

function Monitor({ source }: { source: string }) {
  const invalidate = useThree((state) => state.invalidate);
  const texture = useMemo(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 1152;
    canvas.height = 648;
    const result = new CanvasTexture(canvas);
    result.colorSpace = SRGBColorSpace;
    return result;
  }, []);
  useEffect(() => () => texture.dispose(), [texture]);
  useEffect(() => {
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (!active) return;
      const canvas = texture.image as HTMLCanvasElement;
      const context = canvas.getContext("2d");
      if (!context) return;
      context.fillStyle = "#101216";
      context.fillRect(0, 0, canvas.width, canvas.height);
      const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
      const width = image.width * scale;
      const height = image.height * scale;
      context.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
      texture.needsUpdate = true;
      invalidate();
    };
    image.src = source;
    return () => { active = false; image.onload = null; };
  }, [source, texture, invalidate]);
  return (
    <group>
      <mesh castShadow>
        <boxGeometry args={[3.8, 2.25, 0.12]} />
        <meshStandardMaterial color="#3a404c" metalness={0.65} roughness={0.3} />
      </mesh>
      <mesh position={[0, 0.02, 0.065]}>
        <planeGeometry args={[3.62, 2.036]} />
        <meshBasicMaterial map={texture} toneMapped={false} />
      </mesh>
      <mesh position={[0, -1.37, -0.03]} castShadow>
        <boxGeometry args={[0.16, 0.55, 0.14]} />
        <meshStandardMaterial color="#647180" metalness={0.75} roughness={0.24} />
      </mesh>
      <mesh position={[0, -1.64, 0.06]} castShadow>
        <boxGeometry args={[1.05, 0.07, 0.6]} />
        <meshStandardMaterial color="#647180" metalness={0.75} roughness={0.24} />
      </mesh>
    </group>
  );
}

export default function AgentDesktopScene({ source, label }: { source: string; label: string }) {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const change = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", change);
    return () => document.removeEventListener("visibilitychange", change);
  }, []);
  return (
    <div className="navin-agent-desktop-scene">
      <Canvas
        role="img" aria-label={label} shadows dpr={[1, 2]} gl={{ antialias: true }}
        frameloop={visible ? "demand" : "never"} camera={{ position: [0.5, 0.4, 5.4], fov: 50 }}
        fallback={<img src={source} alt={label} style={{ width: "100%", height: "100%", objectFit: "contain" }} />}
      >
        <color attach="background" args={["#101216"]} />
        <ambientLight intensity={0.65} />
        <directionalLight position={[3, 4, 5]} intensity={2} castShadow />
        <Monitor source={source} />
        <Environment resolution={64}>
          <Lightformer position={[0, 4, 2]} rotation={[Math.PI / 2, 0, 0]} intensity={2} scale={[8, 4, 1]} />
          <Lightformer position={[-4, 1, 2]} rotation={[0, Math.PI / 2, 0]} intensity={1.5} scale={[3, 5, 1]} />
        </Environment>
        <ContactShadows position={[0, -1.7, 0]} opacity={0.5} scale={9} blur={2.5} far={4} frames={1} />
        <OrbitControls enablePan={false} enableDamping dampingFactor={0.12} minDistance={3.3} maxDistance={8} minPolarAngle={0.65} maxPolarAngle={1.65} target={[0, -0.2, 0]} />
      </Canvas>
    </div>
  );
}
