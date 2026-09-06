/**
 * Force-directed placement for the project graph.
 *
 * Columns by file kind told you what a file was, which the colour already says,
 * and never showed what the graph is for: which files cluster together. A spring
 * model puts neighbours near each other and pushes the rest apart, so a module
 * and its dependents read as one group.
 *
 * The layout is deterministic. A graph that lands somewhere new on every reload
 * cannot be recognised, so nodes start on a fixed spiral rather than at random.
 */

export interface LayoutNode {
  id: string;
}

export interface LayoutEdge {
  source: string;
  target: string;
}

export interface Point {
  x: number;
  y: number;
}

export interface LayoutResult {
  positions: Map<string, Point>;
  /** Bounds of the placed graph, padding included. */
  width: number;
  height: number;
}

export interface LayoutOptions {
  /** Room each node claims, which sets how far apart the springs settle. */
  areaPerNode?: number;
  /** Space a label needs to the right of its node before it meets a neighbour. */
  labelAllowance?: number;
  /** Closest two nodes may sit once the springs have settled. */
  minDistance?: number;
  /** Shape the packed components aim for, as width / height. */
  aspect?: number;
  padding?: number;
}

const DEFAULTS = {
  areaPerNode: 5200,
  labelAllowance: 104,
  minDistance: 30,
  aspect: 16 / 9,
  padding: 36,
} satisfies Required<LayoutOptions>;

/** Angle that spreads successive points most evenly around a spiral. */
const GOLDEN_ANGLE = 2.399963229728653;

/** Height of one row in the grid of unconnected files. */
const ORPHAN_ROW_HEIGHT = 30;

interface Component {
  ids: string[];
  positions: Map<string, Point>;
  width: number;
  height: number;
}

/**
 * Distance at which repulsion and one edge balance out, so the length an edge
 * settles at. Both the spring model and the gaps between groups are measured in
 * it: a gap narrower than one edge would read as a link that is merely missing.
 */
function idealDistance(config: Required<LayoutOptions>): number {
  return Math.sqrt(config.areaPerNode);
}

export function layoutGraph(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  options: LayoutOptions = {},
): LayoutResult {
  const config = { ...DEFAULTS, ...options };
  const positions = new Map<string, Point>();
  if (nodes.length === 0) return { positions, width: 0, height: 0 };

  const ids = nodes.map((node) => node.id);
  const present = new Set(ids);
  const links = edges.filter(
    (edge) => edge.source !== edge.target && present.has(edge.source) && present.has(edge.target),
  );

  const groups = splitComponents(ids, links);
  const components = groups
    .filter((group) => group.length > 1)
    .map((group) => layoutComponent(group, links, config));

  // Files nothing imports have no shape to find, so simulating them only spends
  // their repulsion on spreading a cloud of dots. A grid reads as the list they
  // are, and leaves the room to the part of the graph that has structure.
  const orphans = groups.filter((group) => group.length === 1).map((group) => group[0]);
  if (orphans.length > 0) components.push(orphanGrid(orphans, config));

  return packComponents(components, config);
}

function orphanGrid(ids: readonly string[], config: Required<LayoutOptions>): Component {
  const cellWidth = config.labelAllowance + 16;
  const columns = Math.max(
    1,
    Math.min(ids.length, Math.round(Math.sqrt((ids.length * ORPHAN_ROW_HEIGHT * config.aspect) / cellWidth))),
  );
  const rows = Math.ceil(ids.length / columns);
  const width = (columns - 1) * cellWidth + config.labelAllowance;
  // A column count is a whole number, so the grid rarely lands on the shape of
  // the panel. Rows are then spread to take the height rather than leaving a
  // band of nothing under a list that had room to breathe.
  const rowHeight =
    rows > 1
      ? Math.min(ORPHAN_ROW_HEIGHT * 2.2, Math.max(ORPHAN_ROW_HEIGHT, width / config.aspect / (rows - 1)))
      : ORPHAN_ROW_HEIGHT;

  const positions = new Map<string, Point>();
  ids.forEach((id, at) => {
    positions.set(id, {
      x: (at % columns) * cellWidth,
      y: Math.floor(at / columns) * rowHeight,
    });
  });
  return {
    ids: [...ids],
    positions,
    width,
    height: (rows - 1) * rowHeight,
  };
}

/**
 * Group nodes that can reach each other.
 *
 * Simulating everything at once stacks unrelated groups on the same centre,
 * because nothing but repulsion separates them and repulsion alone cannot decide
 * which side each belongs on.
 */
function splitComponents(ids: readonly string[], edges: readonly LayoutEdge[]): string[][] {
  const neighbours = new Map<string, string[]>();
  for (const id of ids) neighbours.set(id, []);
  for (const edge of edges) {
    neighbours.get(edge.source)!.push(edge.target);
    neighbours.get(edge.target)!.push(edge.source);
  }

  const seen = new Set<string>();
  const components: string[][] = [];
  for (const id of ids) {
    if (seen.has(id)) continue;
    const group: string[] = [];
    const queue = [id];
    seen.add(id);
    while (queue.length > 0) {
      const current = queue.pop()!;
      group.push(current);
      for (const neighbour of neighbours.get(current)!) {
        if (seen.has(neighbour)) continue;
        seen.add(neighbour);
        queue.push(neighbour);
      }
    }
    components.push(group);
  }
  // Largest first, so the structure worth reading lands top-left.
  return components.sort((a, b) => b.length - a.length);
}

/** Iterations worth spending: small graphs settle fully, large ones roughly. */
function iterationsFor(count: number): number {
  if (count <= 2) return 0;
  if (count <= 60) return 320;
  if (count <= 200) return 200;
  return 120;
}

function layoutComponent(
  ids: readonly string[],
  edges: readonly LayoutEdge[],
  config: Required<LayoutOptions>,
): Component {
  const count = ids.length;
  const side = Math.sqrt(count * config.areaPerNode);
  // Fruchterman-Reingold's ideal edge length: the spacing at which repulsion
  // between two nodes and the pull of one edge cancel out.
  const k = Math.sqrt((side * side) / count);

  // Springs alone settle into a round blob, and a panel is not round. Stretching
  // the start and easing the pull sideways gives the cluster the shape of the
  // room it has to live in.
  const stretch = Math.sqrt(config.aspect);

  const index = new Map(ids.map((id, at) => [id, at]));
  const xs = new Float64Array(count);
  const ys = new Float64Array(count);
  for (let i = 0; i < count; i += 1) {
    // A spiral, not a circle: a circle leaves the middle empty and every node
    // has to cross the others to reach its place.
    const radius = (side / 2) * Math.sqrt((i + 0.5) / count);
    const angle = i * GOLDEN_ANGLE;
    xs[i] = Math.cos(angle) * radius * stretch;
    ys[i] = (Math.sin(angle) * radius) / stretch;
  }

  const local = edges
    .filter((edge) => index.has(edge.source) && index.has(edge.target))
    .map((edge) => [index.get(edge.source)!, index.get(edge.target)!] as const);

  const dx = new Float64Array(count);
  const dy = new Float64Array(count);
  const iterations = iterationsFor(count);
  const startTemperature = side * 0.1;

  for (let step = 0; step < iterations; step += 1) {
    dx.fill(0);
    dy.fill(0);

    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        let vx = xs[i] - xs[j];
        let vy = ys[i] - ys[j];
        let distance = Math.hypot(vx, vy);
        if (distance < 0.01) {
          // Two nodes on the same spot have no direction to separate along, so
          // give them one derived from their order rather than a random nudge.
          vx = ((i % 7) - 3) * 0.01 + 0.001;
          vy = ((j % 5) - 2) * 0.01 + 0.001;
          distance = Math.hypot(vx, vy);
        }
        const push = (k * k) / distance;
        const ux = (vx / distance) * push;
        const uy = (vy / distance) * push;
        dx[i] += ux;
        dy[i] += uy;
        dx[j] -= ux;
        dy[j] -= uy;
      }
    }

    for (const [source, target] of local) {
      const vx = xs[source] - xs[target];
      const vy = ys[source] - ys[target];
      const distance = Math.max(0.01, Math.hypot(vx, vy));
      const pull = (distance * distance) / k;
      const ux = (vx / distance) * pull;
      const uy = (vy / distance) * pull;
      dx[source] -= ux;
      dy[source] -= uy;
      dx[target] += ux;
      dy[target] += uy;
    }

    // A mild pull to the middle. Without it a sparse component keeps unfolding
    // into a thin thread that no screen is shaped like.
    for (let i = 0; i < count; i += 1) {
      dx[i] -= (xs[i] * 0.035) / stretch;
      dy[i] -= ys[i] * 0.035 * stretch;
    }

    const temperature = startTemperature * (1 - step / iterations);
    for (let i = 0; i < count; i += 1) {
      const distance = Math.hypot(dx[i], dy[i]);
      if (distance < 1e-9) continue;
      const limit = Math.min(distance, temperature);
      xs[i] += (dx[i] / distance) * limit;
      ys[i] += (dy[i] / distance) * limit;
    }
  }

  relaxCollisions(xs, ys, count, config);

  return finishComponent(ids, xs, ys, config);
}

/**
 * Sideways distance the layout guarantees between any two nodes.
 *
 * Exported because the renderer has to know it: the gap is in graph units while
 * a label is drawn at a fixed size on screen, so how much of a name fits depends
 * on the zoom, and only this number says how much room there is to fit it in.
 */
export function labelSeparation(labelAllowance: number = DEFAULTS.labelAllowance): number {
  return labelAllowance * 0.9;
}

/** Half the room a node plus its label occupies, used to keep names readable. */
function labelBox(config: Required<LayoutOptions>): { halfWidth: number; halfHeight: number } {
  return { halfWidth: labelSeparation(config.labelAllowance) / 2, halfHeight: 10 };
}

/**
 * Push apart nodes that ended up on top of each other.
 *
 * The spring model balances forces, not overlap: two nodes pulled by the same
 * dense neighbour can settle a few pixels apart, where their labels become one
 * unreadable smear. Two passes, because a node is a dot but reads as a dot plus
 * a name: the dots need distance in any direction, the names need it sideways.
 */
function relaxCollisions(
  xs: Float64Array,
  ys: Float64Array,
  count: number,
  config: Required<LayoutOptions>,
): void {
  const { minDistance } = config;
  for (let pass = 0; pass < 12; pass += 1) {
    let moved = false;
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        const vx = xs[i] - xs[j];
        const vy = ys[i] - ys[j];
        const distance = Math.hypot(vx, vy);
        if (distance >= minDistance) continue;
        moved = true;
        const shift = (minDistance - Math.max(distance, 0.01)) / 2;
        const ux = distance < 0.01 ? (i % 2 === 0 ? 1 : -1) : vx / distance;
        const uy = distance < 0.01 ? (j % 2 === 0 ? 1 : -1) : vy / distance;
        xs[i] += ux * shift;
        ys[i] += uy * shift;
        xs[j] -= ux * shift;
        ys[j] -= uy * shift;
      }
    }
    if (!moved) break;
  }

  separateLabels(xs, ys, count, config);
}

/**
 * Separate names that would print over each other.
 *
 * A label is wide and short, so two nodes far enough apart for their dots can
 * still collide as text. Each overlap is resolved along the axis that needs the
 * smaller move, which is nearly always vertical and leaves clusters in place.
 */
function separateLabels(
  xs: Float64Array,
  ys: Float64Array,
  count: number,
  config: Required<LayoutOptions>,
): void {
  const { halfWidth, halfHeight } = labelBox(config);
  for (let pass = 0; pass < 10; pass += 1) {
    let moved = false;
    for (let i = 0; i < count; i += 1) {
      for (let j = i + 1; j < count; j += 1) {
        const overlapX = 2 * halfWidth - Math.abs(xs[i] - xs[j]);
        const overlapY = 2 * halfHeight - Math.abs(ys[i] - ys[j]);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        if (overlapY / halfHeight <= overlapX / halfWidth) {
          const shift = (overlapY / 2 + 0.5) * (ys[i] >= ys[j] ? 1 : -1);
          ys[i] += shift;
          ys[j] -= shift;
        } else {
          const shift = (overlapX / 2 + 0.5) * (xs[i] >= xs[j] ? 1 : -1);
          xs[i] += shift;
          xs[j] -= shift;
        }
      }
    }
    if (!moved) break;
  }
}

/** Move a component's points to origin and measure the box they need. */
function finishComponent(
  ids: readonly string[],
  xs: Float64Array,
  ys: Float64Array,
  config: Required<LayoutOptions>,
): Component {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < ids.length; i += 1) {
    minX = Math.min(minX, xs[i]);
    minY = Math.min(minY, ys[i]);
    maxX = Math.max(maxX, xs[i]);
    maxY = Math.max(maxY, ys[i]);
  }

  const positions = new Map<string, Point>();
  ids.forEach((id, i) => {
    positions.set(id, { x: xs[i] - minX, y: ys[i] - minY });
  });

  return {
    ids: [...ids],
    positions,
    // The label sits to the right of its node, so the box has to hold it too or
    // names run into the next component.
    width: maxX - minX + config.labelAllowance,
    height: maxY - minY,
  };
}

/**
 * Lay the components out in rows, biggest first.
 *
 * Rows rather than one long line: a screen is wider than it is tall, and a row
 * of single unconnected files reads as a tidy list once it wraps.
 */
function packComponents(
  components: readonly Component[],
  config: Required<LayoutOptions>,
): LayoutResult {
  const gap = idealDistance(config) * 1.7;
  const totalArea = components.reduce(
    (sum, component) => sum + (component.width + gap) * (component.height + gap),
    0,
  );
  const widest = Math.max(...components.map((component) => component.width));
  const rowWidth = Math.max(widest, Math.sqrt(totalArea * config.aspect));

  const positions = new Map<string, Point>();
  let cursorX = 0;
  let cursorY = 0;
  let rowHeight = 0;
  let usedWidth = 0;

  for (const component of components) {
    if (cursorX > 0 && cursorX + component.width > rowWidth) {
      cursorX = 0;
      cursorY += rowHeight + gap;
      rowHeight = 0;
    }
    for (const id of component.ids) {
      const point = component.positions.get(id)!;
      positions.set(id, {
        x: config.padding + cursorX + point.x,
        y: config.padding + cursorY + point.y,
      });
    }
    cursorX += component.width + gap;
    usedWidth = Math.max(usedWidth, cursorX - gap);
    rowHeight = Math.max(rowHeight, component.height);
  }

  return {
    positions,
    width: usedWidth + config.padding * 2,
    height: cursorY + rowHeight + config.padding * 2,
  };
}
