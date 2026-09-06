//! Fruchterman-Reingold force layout, ported from webui/src/lib/force-layout.ts.
//!
//! Deterministic (golden-angle spiral init, no RNG). Sticky positions keep
//! unchanged nodes fixed while new ones settle around them.

use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};

const GOLDEN_ANGLE: f64 = 2.399_963_229_728_653;
const ORPHAN_ROW_HEIGHT: f64 = 30.0;

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LayoutOptions {
    #[serde(default = "default_area")]
    pub area_per_node: f64,
    #[serde(default = "default_label")]
    pub label_allowance: f64,
    #[serde(default = "default_min_distance")]
    pub min_distance: f64,
    #[serde(default = "default_aspect")]
    pub aspect: f64,
    #[serde(default = "default_padding")]
    pub padding: f64,
}

impl Default for LayoutOptions {
    fn default() -> Self {
        Self {
            area_per_node: default_area(),
            label_allowance: default_label(),
            min_distance: default_min_distance(),
            aspect: default_aspect(),
            padding: default_padding(),
        }
    }
}

fn default_area() -> f64 {
    5200.0
}
fn default_label() -> f64 {
    104.0
}
fn default_min_distance() -> f64 {
    30.0
}
fn default_aspect() -> f64 {
    16.0 / 9.0
}
fn default_padding() -> f64 {
    36.0
}

pub struct LayoutResult {
    pub positions: HashMap<String, Point>,
    pub width: f64,
    pub height: f64,
}

struct Component {
    ids: Vec<String>,
    positions: HashMap<String, Point>,
    width: f64,
    height: f64,
}

fn ideal_distance(config: &LayoutOptions) -> f64 {
    config.area_per_node.sqrt()
}

pub fn layout_graph(
    ids: &[String],
    edges: &[(String, String)],
    config: &LayoutOptions,
    sticky: &HashMap<String, Point>,
) -> LayoutResult {
    if ids.is_empty() {
        return LayoutResult {
            positions: HashMap::new(),
            width: 0.0,
            height: 0.0,
        };
    }

    let present: HashSet<&str> = ids.iter().map(|s| s.as_str()).collect();
    let links: Vec<(&str, &str)> = edges
        .iter()
        .filter(|(s, t)| s != t && present.contains(s.as_str()) && present.contains(t.as_str()))
        .map(|(s, t)| (s.as_str(), t.as_str()))
        .collect();

    let id_refs: Vec<&str> = ids.iter().map(|s| s.as_str()).collect();
    let groups = split_components(&id_refs, &links);

    let mut components: Vec<Component> = groups
        .iter()
        .filter(|g| g.len() > 1)
        .map(|g| layout_component(g, &links, config))
        .collect();

    let orphans: Vec<&str> = groups
        .iter()
        .filter(|g| g.len() == 1)
        .map(|g| g[0])
        .collect();
    if !orphans.is_empty() {
        components.push(orphan_grid(&orphans, config));
    }

    let mut result = pack_components(&components, config);

    // Restore sticky coordinates for nodes that still exist so live diffs do
    // not reshuffle the whole map. New nodes keep freshly computed positions.
    if !sticky.is_empty() {
        for (id, point) in sticky {
            if result.positions.contains_key(id) {
                result.positions.insert(id.clone(), *point);
            }
        }
        let (min_x, min_y, max_x, max_y) = bounds(&result.positions);
        result.width = (max_x - min_x).max(0.0) + config.padding * 2.0 + config.label_allowance;
        result.height = (max_y - min_y).max(0.0) + config.padding * 2.0;
    }

    result
}

fn orphan_grid(ids: &[&str], config: &LayoutOptions) -> Component {
    let cell_width = config.label_allowance + 16.0;
    let columns = ((ids.len() as f64 * ORPHAN_ROW_HEIGHT * config.aspect) / cell_width)
        .sqrt()
        .round()
        .clamp(1.0, ids.len() as f64) as usize;
    let columns = columns.max(1);
    let rows = ids.len().div_ceil(columns);
    let width = (columns.saturating_sub(1) as f64) * cell_width + config.label_allowance;
    let row_height = if rows > 1 {
        (width / config.aspect / (rows as f64 - 1.0))
            .clamp(ORPHAN_ROW_HEIGHT, ORPHAN_ROW_HEIGHT * 2.2)
    } else {
        ORPHAN_ROW_HEIGHT
    };

    let mut positions = HashMap::new();
    for (at, id) in ids.iter().enumerate() {
        positions.insert(
            (*id).to_string(),
            Point {
                x: (at % columns) as f64 * cell_width,
                y: (at / columns) as f64 * row_height,
            },
        );
    }
    Component {
        ids: ids.iter().map(|s| (*s).to_string()).collect(),
        positions,
        width,
        height: (rows.saturating_sub(1) as f64) * row_height,
    }
}

fn split_components<'a>(ids: &[&'a str], edges: &[(&'a str, &'a str)]) -> Vec<Vec<&'a str>> {
    let mut neighbours: HashMap<&str, Vec<&str>> = HashMap::new();
    for id in ids {
        neighbours.insert(*id, Vec::new());
    }
    for (s, t) in edges {
        neighbours.get_mut(*s).unwrap().push(*t);
        neighbours.get_mut(*t).unwrap().push(*s);
    }

    let mut seen = HashSet::new();
    let mut components = Vec::new();
    for id in ids {
        if seen.contains(id) {
            continue;
        }
        let mut group = Vec::new();
        let mut stack = vec![*id];
        seen.insert(*id);
        while let Some(current) = stack.pop() {
            group.push(current);
            for neighbour in neighbours.get(current).into_iter().flatten() {
                if seen.insert(*neighbour) {
                    stack.push(*neighbour);
                }
            }
        }
        components.push(group);
    }
    components.sort_by(|a, b| b.len().cmp(&a.len()));
    components
}

fn iterations_for(count: usize) -> usize {
    if count <= 2 {
        0
    } else if count <= 60 {
        320
    } else if count <= 200 {
        200
    } else {
        120
    }
}

fn layout_component(ids: &[&str], edges: &[(&str, &str)], config: &LayoutOptions) -> Component {
    let count = ids.len();
    let side = (count as f64 * config.area_per_node).sqrt();
    let k = (side * side / count as f64).sqrt();
    let stretch = config.aspect.sqrt();

    let index: HashMap<&str, usize> = ids.iter().enumerate().map(|(i, id)| (*id, i)).collect();
    let mut xs = vec![0.0f64; count];
    let mut ys = vec![0.0f64; count];

    for (i, _) in ids.iter().enumerate() {
        let radius = (side / 2.0) * ((i as f64 + 0.5) / count as f64).sqrt();
        let angle = i as f64 * GOLDEN_ANGLE;
        xs[i] = angle.cos() * radius * stretch;
        ys[i] = (angle.sin() * radius) / stretch;
    }

    let local: Vec<(usize, usize)> = edges
        .iter()
        .filter_map(|(s, t)| Some((*index.get(s)?, *index.get(t)?)))
        .collect();

    let iterations = iterations_for(count);
    let start_temperature = side * 0.1;
    let mut dx = vec![0.0f64; count];
    let mut dy = vec![0.0f64; count];

    for step in 0..iterations {
        dx.fill(0.0);
        dy.fill(0.0);

        for i in 0..count {
            for j in (i + 1)..count {
                let mut vx = xs[i] - xs[j];
                let mut vy = ys[i] - ys[j];
                let mut distance = (vx * vx + vy * vy).sqrt();
                if distance < 0.01 {
                    vx = ((i % 7) as f64 - 3.0) * 0.01 + 0.001;
                    vy = ((j % 5) as f64 - 2.0) * 0.01 + 0.001;
                    distance = (vx * vx + vy * vy).sqrt();
                }
                let push = (k * k) / distance;
                let ux = (vx / distance) * push;
                let uy = (vy / distance) * push;
                dx[i] += ux;
                dy[i] += uy;
                dx[j] -= ux;
                dy[j] -= uy;
            }
        }

        for (source, target) in &local {
            let vx = xs[*source] - xs[*target];
            let vy = ys[*source] - ys[*target];
            let distance = (vx * vx + vy * vy).sqrt().max(0.01);
            let pull = (distance * distance) / k;
            let ux = (vx / distance) * pull;
            let uy = (vy / distance) * pull;
            dx[*source] -= ux;
            dy[*source] -= uy;
            dx[*target] += ux;
            dy[*target] += uy;
        }

        for i in 0..count {
            dx[i] -= (xs[i] * 0.035) / stretch;
            dy[i] -= ys[i] * 0.035 * stretch;
        }

        let temperature = start_temperature * (1.0 - step as f64 / iterations as f64);
        for i in 0..count {
            let distance = (dx[i] * dx[i] + dy[i] * dy[i]).sqrt();
            if distance < 1e-9 {
                continue;
            }
            let limit = distance.min(temperature);
            xs[i] += (dx[i] / distance) * limit;
            ys[i] += (dy[i] / distance) * limit;
        }
    }

    relax_collisions(&mut xs, &mut ys, config);
    finish_component(ids, &xs, &ys, config)
}

fn relax_collisions(xs: &mut [f64], ys: &mut [f64], config: &LayoutOptions) {
    let count = xs.len();
    let min_distance = config.min_distance;
    for _ in 0..12 {
        let mut moved = false;
        for i in 0..count {
            for j in (i + 1)..count {
                let vx = xs[i] - xs[j];
                let vy = ys[i] - ys[j];
                let distance = (vx * vx + vy * vy).sqrt();
                if distance >= min_distance {
                    continue;
                }
                moved = true;
                let shift = (min_distance - distance.max(0.01)) / 2.0;
                let ux = if distance < 0.01 {
                    if i % 2 == 0 {
                        1.0
                    } else {
                        -1.0
                    }
                } else {
                    vx / distance
                };
                let uy = if distance < 0.01 {
                    if j % 2 == 0 {
                        1.0
                    } else {
                        -1.0
                    }
                } else {
                    vy / distance
                };
                xs[i] += ux * shift;
                ys[i] += uy * shift;
                xs[j] -= ux * shift;
                ys[j] -= uy * shift;
            }
        }
        if !moved {
            break;
        }
    }
    separate_labels(xs, ys, config);
}

fn separate_labels(xs: &mut [f64], ys: &mut [f64], config: &LayoutOptions) {
    let half_width = config.label_allowance * 0.9 / 2.0;
    let half_height = 10.0;
    let count = xs.len();
    for _ in 0..10 {
        let mut moved = false;
        for i in 0..count {
            for j in (i + 1)..count {
                let overlap_x = 2.0 * half_width - (xs[i] - xs[j]).abs();
                let overlap_y = 2.0 * half_height - (ys[i] - ys[j]).abs();
                if overlap_x <= 0.0 || overlap_y <= 0.0 {
                    continue;
                }
                moved = true;
                if overlap_y / half_height <= overlap_x / half_width {
                    let dir = if ys[i] >= ys[j] { 1.0 } else { -1.0 };
                    let shift = (overlap_y / 2.0 + 0.5) * dir;
                    ys[i] += shift;
                    ys[j] -= shift;
                } else {
                    let dir = if xs[i] >= xs[j] { 1.0 } else { -1.0 };
                    let shift = (overlap_x / 2.0 + 0.5) * dir;
                    xs[i] += shift;
                    xs[j] -= shift;
                }
            }
        }
        if !moved {
            break;
        }
    }
}

fn finish_component(ids: &[&str], xs: &[f64], ys: &[f64], config: &LayoutOptions) -> Component {
    let mut min_x = f64::INFINITY;
    let mut min_y = f64::INFINITY;
    let mut max_x = f64::NEG_INFINITY;
    let mut max_y = f64::NEG_INFINITY;
    for i in 0..ids.len() {
        min_x = min_x.min(xs[i]);
        min_y = min_y.min(ys[i]);
        max_x = max_x.max(xs[i]);
        max_y = max_y.max(ys[i]);
    }
    let mut positions = HashMap::new();
    for (i, id) in ids.iter().enumerate() {
        positions.insert(
            (*id).to_string(),
            Point {
                x: xs[i] - min_x,
                y: ys[i] - min_y,
            },
        );
    }
    Component {
        ids: ids.iter().map(|s| (*s).to_string()).collect(),
        positions,
        width: max_x - min_x + config.label_allowance,
        height: max_y - min_y,
    }
}

fn bounds(positions: &HashMap<String, Point>) -> (f64, f64, f64, f64) {
    let mut min_x = f64::INFINITY;
    let mut min_y = f64::INFINITY;
    let mut max_x = f64::NEG_INFINITY;
    let mut max_y = f64::NEG_INFINITY;
    for p in positions.values() {
        min_x = min_x.min(p.x);
        min_y = min_y.min(p.y);
        max_x = max_x.max(p.x);
        max_y = max_y.max(p.y);
    }
    if !min_x.is_finite() {
        return (0.0, 0.0, 0.0, 0.0);
    }
    (min_x, min_y, max_x, max_y)
}

fn pack_components(components: &[Component], config: &LayoutOptions) -> LayoutResult {
    if components.is_empty() {
        return LayoutResult {
            positions: HashMap::new(),
            width: 0.0,
            height: 0.0,
        };
    }
    let gap = ideal_distance(config) * 1.7;
    let total_area: f64 = components
        .iter()
        .map(|c| (c.width + gap) * (c.height + gap))
        .sum();
    let widest = components.iter().map(|c| c.width).fold(0.0, f64::max);
    let row_width = widest.max((total_area * config.aspect).sqrt());

    let mut positions = HashMap::new();
    let mut cursor_x = 0.0_f64;
    let mut cursor_y = 0.0_f64;
    let mut row_height = 0.0_f64;
    let mut used_width = 0.0_f64;

    for component in components {
        if cursor_x > 0.0 && cursor_x + component.width > row_width {
            cursor_x = 0.0;
            cursor_y += row_height + gap;
            row_height = 0.0;
        }
        for id in &component.ids {
            let point = component.positions.get(id).copied().unwrap_or(Point { x: 0.0, y: 0.0 });
            positions.insert(
                id.clone(),
                Point {
                    x: config.padding + cursor_x + point.x,
                    y: config.padding + cursor_y + point.y,
                },
            );
        }
        cursor_x += component.width + gap;
        used_width = used_width.max(cursor_x - gap);
        row_height = row_height.max(component.height);
    }

    LayoutResult {
        positions,
        width: used_width + config.padding * 2.0,
        height: cursor_y + row_height + config.padding * 2.0,
    }
}
