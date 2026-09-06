/** Local breakpoint store for the Code workbench (path -> 1-based lines). */

type Listener = () => void;

const byPath = new Map<string, Set<number>>();
const listeners = new Set<Listener>();

function emit() {
  for (const listener of listeners) listener();
}

export function subscribeBreakpoints(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getBreakpointLines(path: string | null | undefined): number[] {
  if (!path) return [];
  const set = byPath.get(path);
  return set ? [...set].sort((a, b) => a - b) : [];
}

export function getAllBreakpoints(): Array<{ path: string; lines: number[] }> {
  return [...byPath.entries()]
    .filter(([, lines]) => lines.size > 0)
    .map(([path, lines]) => ({
      path,
      lines: [...lines].sort((a, b) => a - b),
    }))
    .sort((a, b) => a.path.localeCompare(b.path));
}

export function toggleBreakpoint(path: string, line: number): number[] {
  if (!path || line < 1) return getBreakpointLines(path);
  const set = byPath.get(path) ?? new Set<number>();
  if (set.has(line)) set.delete(line);
  else set.add(line);
  if (set.size === 0) byPath.delete(path);
  else byPath.set(path, set);
  emit();
  return getBreakpointLines(path);
}

export function setBreakpointLines(path: string, lines: number[]): void {
  const cleaned = new Set(
    lines.filter((line) => Number.isInteger(line) && line > 0),
  );
  if (cleaned.size === 0) byPath.delete(path);
  else byPath.set(path, cleaned);
  emit();
}
