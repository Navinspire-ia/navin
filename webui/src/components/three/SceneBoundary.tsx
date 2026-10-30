// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { Component, type ReactNode } from "react";

/**
 * Keeps a 3D scene's failure inside its frame.
 *
 * React Three Fiber rethrows anything that fails in a canvas, starting with
 * "Error creating WebGL context": WebKitGTK without GPU compositing (WSL,
 * boxes without DRM), a lost context, or a blocked driver. With no boundary
 * above, that unmounted the whole interface, which then stayed blank. The
 * scenes are decoration: the frame keeps its size and shows `fallback`.
 */
export class SceneBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    console.warn("3D scene disabled:", error);
  }

  render(): ReactNode {
    return this.state.failed ? (this.props.fallback ?? null) : this.props.children;
  }
}
