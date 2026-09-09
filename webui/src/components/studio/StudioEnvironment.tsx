// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Environment, Lightformer } from "@react-three/drei";

/** Local softboxes keep reflective materials readable without an HDR download. */
export function StudioEnvironment() {
  return (
    <Environment resolution={128} frames={1}>
      <color attach="background" args={["#171d27"]} />
      <Lightformer
        form="rect"
        color="#f8fafc"
        intensity={3}
        position={[0, 4, 2]}
        scale={[5, 4, 1]}
      />
      <Lightformer
        form="rect"
        color="#cbd5e1"
        intensity={2}
        position={[-4, 1, 0]}
        scale={[3, 4, 1]}
      />
      <Lightformer
        form="rect"
        color="#ffffff"
        intensity={1.8}
        position={[3, 1, -3]}
        scale={[2, 4, 1]}
      />
    </Environment>
  );
}
