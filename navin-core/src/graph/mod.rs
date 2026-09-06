//! Project metagraph engine: build, layout, diff, and query.
//!
//! Python still owns discovery and import resolution (`navin.index`). This
//! module takes a JSON snapshot of files + edges and produces the Graph tab
//! payload: degrees, truncation, force layout, package clusters, diffs, and
//! path/impact/hub queries. Every public entry returns JSON so the Python
//! facade can keep a pure-Python fallback when the extension is absent.

mod layout;
mod query;

use std::collections::{HashMap, HashSet};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

use layout::{layout_graph, LayoutOptions, Point};
use query::{query_cluster, query_find, query_hubs, query_impact, query_path};

#[derive(Debug, Clone, Deserialize)]
struct SnapshotIn {
    project_path: String,
    #[serde(default)]
    generation: u64,
    #[serde(default = "default_max_nodes")]
    max_nodes: usize,
    #[serde(default = "default_view")]
    view: String,
    #[serde(default)]
    files: Vec<FileIn>,
    #[serde(default)]
    edges: Vec<[String; 2]>,
    #[serde(default)]
    discovery: String,
    #[serde(default)]
    total_files: usize,
    #[serde(default)]
    symbols: usize,
    #[serde(default)]
    has_metadata: bool,
}

fn default_max_nodes() -> usize {
    6000
}

fn default_view() -> String {
    "files".into()
}

#[derive(Debug, Clone, Deserialize, Serialize)]
struct FileIn {
    id: String,
    kind: String,
    #[serde(default)]
    size: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    symbols: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    role: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    role_source: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    role_stale: Option<bool>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct GraphNode {
    pub id: String,
    pub kind: String,
    pub size: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub role: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub role_source: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub role_stale: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub symbols: Option<usize>,
    pub in_degree: usize,
    pub out_degree: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub struct GraphEdge {
    pub source: String,
    pub target: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphCluster {
    pub id: String,
    pub label: String,
    pub member_ids: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphState {
    pub project_path: String,
    pub generation: u64,
    pub view: String,
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
    pub kinds: HashMap<String, usize>,
    pub has_metadata: bool,
    pub annotated: usize,
    pub annotated_manual: usize,
    pub annotated_stale: usize,
    #[serde(skip_serializing_if = "String::is_empty")]
    pub discovery: String,
    pub truncated: bool,
    pub total_files: usize,
    pub symbols: usize,
    #[serde(default, skip_serializing_if = "HashMap::is_empty")]
    pub positions: HashMap<String, Point>,
    #[serde(default)]
    pub layout_width: f64,
    #[serde(default)]
    pub layout_height: f64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub clusters: Vec<GraphCluster>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphDiff {
    pub added_nodes: Vec<GraphNode>,
    pub removed_nodes: Vec<String>,
    pub updated_nodes: Vec<GraphNode>,
    pub added_edges: Vec<GraphEdge>,
    pub removed_edges: Vec<GraphEdge>,
    pub generation: u64,
}

fn err(msg: impl Into<String>) -> PyErr {
    PyValueError::new_err(msg.into())
}

fn parse_snapshot(snapshot_json: &str) -> PyResult<SnapshotIn> {
    serde_json::from_str(snapshot_json).map_err(|e| err(format!("invalid snapshot: {e}")))
}

fn build_state(snap: SnapshotIn) -> GraphState {
    let mut nodes_by_id: HashMap<String, GraphNode> = HashMap::with_capacity(snap.files.len());
    for file in snap.files {
        nodes_by_id.insert(
            file.id.clone(),
            GraphNode {
                id: file.id,
                kind: file.kind,
                size: file.size,
                role: file.role,
                role_source: file.role_source,
                role_stale: file.role_stale.filter(|v| *v),
                symbols: file.symbols.filter(|n| *n > 0),
                in_degree: 0,
                out_degree: 0,
            },
        );
    }

    let mut edge_set: HashSet<(String, String)> = HashSet::new();
    for pair in snap.edges {
        let source = pair[0].clone();
        let target = pair[1].clone();
        if source == target {
            continue;
        }
        if !nodes_by_id.contains_key(&source) || !nodes_by_id.contains_key(&target) {
            continue;
        }
        edge_set.insert((source, target));
    }

    let mut in_degree: HashMap<String, usize> = HashMap::new();
    let mut out_degree: HashMap<String, usize> = HashMap::new();
    for (source, target) in &edge_set {
        *out_degree.entry(source.clone()).or_default() += 1;
        *in_degree.entry(target.clone()).or_default() += 1;
    }
    for (id, node) in nodes_by_id.iter_mut() {
        node.in_degree = *in_degree.get(id).unwrap_or(&0);
        node.out_degree = *out_degree.get(id).unwrap_or(&0);
    }

    let mut truncated = nodes_by_id.len() > snap.max_nodes;
    if truncated {
        let mut ranked: Vec<&GraphNode> = nodes_by_id.values().collect();
        ranked.sort_by(|a, b| {
            let da = a.in_degree + a.out_degree;
            let db = b.in_degree + b.out_degree;
            db.cmp(&da)
                .then_with(|| b.symbols.unwrap_or(0).cmp(&a.symbols.unwrap_or(0)))
                .then_with(|| a.id.cmp(&b.id))
        });
        let kept: HashSet<String> = ranked
            .into_iter()
            .take(snap.max_nodes)
            .map(|n| n.id.clone())
            .collect();
        nodes_by_id.retain(|id, _| kept.contains(id));
        edge_set.retain(|(s, t)| kept.contains(s) && kept.contains(t));
        // Recompute degrees after truncation.
        in_degree.clear();
        out_degree.clear();
        for (source, target) in &edge_set {
            *out_degree.entry(source.clone()).or_default() += 1;
            *in_degree.entry(target.clone()).or_default() += 1;
        }
        for (id, node) in nodes_by_id.iter_mut() {
            node.in_degree = *in_degree.get(id).unwrap_or(&0);
            node.out_degree = *out_degree.get(id).unwrap_or(&0);
        }
    }

    let view = if snap.view == "packages" {
        "packages"
    } else {
        "files"
    };

    let (nodes, edges, clusters) = if view == "packages" {
        aggregate_packages(&nodes_by_id, &edge_set)
    } else {
        let mut nodes: Vec<GraphNode> = nodes_by_id.into_values().collect();
        nodes.sort_by(|a, b| a.id.cmp(&b.id));
        let mut edges: Vec<GraphEdge> = edge_set
            .into_iter()
            .map(|(source, target)| GraphEdge { source, target })
            .collect();
        edges.sort_by(|a, b| (&a.source, &a.target).cmp(&(&b.source, &b.target)));
        (nodes, edges, Vec::new())
    };

    // Truncation flag only meaningful for the file view; packages always fit.
    if view == "packages" {
        truncated = false;
    }

    let mut kinds: HashMap<String, usize> = HashMap::new();
    let mut annotated = 0usize;
    let mut annotated_manual = 0usize;
    let mut annotated_stale = 0usize;
    for node in &nodes {
        *kinds.entry(node.kind.clone()).or_default() += 1;
        if node.role.is_some() {
            annotated += 1;
        }
        if node.role_source.as_deref() == Some("manual") {
            annotated_manual += 1;
        }
        if node.role_stale == Some(true) {
            annotated_stale += 1;
        }
    }

    GraphState {
        project_path: snap.project_path,
        generation: snap.generation,
        view: view.into(),
        nodes,
        edges,
        kinds,
        has_metadata: snap.has_metadata,
        annotated,
        annotated_manual,
        annotated_stale,
        discovery: snap.discovery,
        truncated,
        total_files: snap.total_files,
        symbols: snap.symbols,
        positions: HashMap::new(),
        layout_width: 0.0,
        layout_height: 0.0,
        clusters,
    }
}

fn package_key(path: &str) -> String {
    match path.find('/') {
        Some(idx) if idx > 0 => path[..idx].to_string(),
        _ => "(root)".to_string(),
    }
}

fn dominant_kind(members: &[&GraphNode]) -> String {
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for node in members {
        *counts.entry(node.kind.as_str()).or_default() += 1;
    }
    counts
        .into_iter()
        .max_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(b.0)))
        .map(|(k, _)| k.to_string())
        .unwrap_or_else(|| "other".into())
}

fn aggregate_packages(
    nodes_by_id: &HashMap<String, GraphNode>,
    edge_set: &HashSet<(String, String)>,
) -> (Vec<GraphNode>, Vec<GraphEdge>, Vec<GraphCluster>) {
    let mut groups: HashMap<String, Vec<&GraphNode>> = HashMap::new();
    for node in nodes_by_id.values() {
        groups.entry(package_key(&node.id)).or_default().push(node);
    }

    let mut clusters: Vec<GraphCluster> = Vec::with_capacity(groups.len());
    let mut nodes: Vec<GraphNode> = Vec::with_capacity(groups.len());
    let mut package_of: HashMap<String, String> = HashMap::new();

    let mut keys: Vec<String> = groups.keys().cloned().collect();
    keys.sort();

    for key in &keys {
        let members = groups.get(key).unwrap();
        let mut member_ids: Vec<String> = members.iter().map(|n| n.id.clone()).collect();
        member_ids.sort();
        for id in &member_ids {
            package_of.insert(id.clone(), key.clone());
        }
        let kind = dominant_kind(members);
        let size: u64 = members.iter().map(|n| n.size).sum();
        let symbols: usize = members.iter().map(|n| n.symbols.unwrap_or(0)).sum();
        let role = Some(format!("{} files", member_ids.len()));
        nodes.push(GraphNode {
            id: key.clone(),
            kind: kind.clone(),
            size,
            role,
            role_source: Some("auto".into()),
            role_stale: None,
            symbols: if symbols > 0 { Some(symbols) } else { None },
            in_degree: 0,
            out_degree: 0,
        });
        clusters.push(GraphCluster {
            id: key.clone(),
            label: key.clone(),
            member_ids,
            kind: Some(kind),
        });
    }

    let mut pkg_edges: HashSet<(String, String)> = HashSet::new();
    for (source, target) in edge_set {
        let Some(ps) = package_of.get(source) else {
            continue;
        };
        let Some(pt) = package_of.get(target) else {
            continue;
        };
        if ps != pt {
            pkg_edges.insert((ps.clone(), pt.clone()));
        }
    }

    let mut in_degree: HashMap<String, usize> = HashMap::new();
    let mut out_degree: HashMap<String, usize> = HashMap::new();
    for (source, target) in &pkg_edges {
        *out_degree.entry(source.clone()).or_default() += 1;
        *in_degree.entry(target.clone()).or_default() += 1;
    }
    for node in &mut nodes {
        node.in_degree = *in_degree.get(&node.id).unwrap_or(&0);
        node.out_degree = *out_degree.get(&node.id).unwrap_or(&0);
    }

    let mut edges: Vec<GraphEdge> = pkg_edges
        .into_iter()
        .map(|(source, target)| GraphEdge { source, target })
        .collect();
    edges.sort_by(|a, b| (&a.source, &a.target).cmp(&(&b.source, &b.target)));
    nodes.sort_by(|a, b| a.id.cmp(&b.id));
    clusters.sort_by(|a, b| a.id.cmp(&b.id));
    (nodes, edges, clusters)
}

fn diff_states(prev: &GraphState, next: &GraphState) -> GraphDiff {
    let prev_nodes: HashMap<&str, &GraphNode> =
        prev.nodes.iter().map(|n| (n.id.as_str(), n)).collect();
    let next_nodes: HashMap<&str, &GraphNode> =
        next.nodes.iter().map(|n| (n.id.as_str(), n)).collect();

    let mut added_nodes = Vec::new();
    let mut updated_nodes = Vec::new();
    let mut removed_nodes = Vec::new();

    for (id, node) in &next_nodes {
        match prev_nodes.get(id) {
            None => added_nodes.push((*node).clone()),
            Some(old) if *old != *node => updated_nodes.push((*node).clone()),
            _ => {}
        }
    }
    for id in prev_nodes.keys() {
        if !next_nodes.contains_key(id) {
            removed_nodes.push((*id).to_string());
        }
    }
    added_nodes.sort_by(|a, b| a.id.cmp(&b.id));
    updated_nodes.sort_by(|a, b| a.id.cmp(&b.id));
    removed_nodes.sort();

    let prev_edges: HashSet<&GraphEdge> = prev.edges.iter().collect();
    let next_edges: HashSet<&GraphEdge> = next.edges.iter().collect();
    let mut added_edges: Vec<GraphEdge> = next_edges
        .difference(&prev_edges)
        .map(|e| (*e).clone())
        .collect();
    let mut removed_edges: Vec<GraphEdge> = prev_edges
        .difference(&next_edges)
        .map(|e| (*e).clone())
        .collect();
    added_edges.sort_by(|a, b| (&a.source, &a.target).cmp(&(&b.source, &b.target)));
    removed_edges.sort_by(|a, b| (&a.source, &a.target).cmp(&(&b.source, &b.target)));

    GraphDiff {
        added_nodes,
        removed_nodes,
        updated_nodes,
        added_edges,
        removed_edges,
        generation: next.generation,
    }
}

#[pyfunction]
pub fn graph_build(py: Python<'_>, snapshot_json: String) -> PyResult<String> {
    let snap = parse_snapshot(&snapshot_json)?;
    let state = py.allow_threads(move || build_state(snap));
    serde_json::to_string(&state).map_err(|e| err(format!("serialize state: {e}")))
}

#[pyfunction]
#[pyo3(signature = (state_json, options_json=None, sticky_json=None))]
pub fn graph_layout(
    py: Python<'_>,
    state_json: String,
    options_json: Option<String>,
    sticky_json: Option<String>,
) -> PyResult<String> {
    let mut state: GraphState =
        serde_json::from_str(&state_json).map_err(|e| err(format!("invalid state: {e}")))?;
    let options: LayoutOptions = match options_json.as_deref() {
        Some(raw) if !raw.is_empty() => {
            serde_json::from_str(raw).map_err(|e| err(format!("invalid layout options: {e}")))?
        }
        _ => LayoutOptions::default(),
    };
    let sticky: HashMap<String, Point> = match sticky_json.as_deref() {
        Some(raw) if !raw.is_empty() => {
            serde_json::from_str(raw).map_err(|e| err(format!("invalid sticky positions: {e}")))?
        }
        _ => HashMap::new(),
    };

    let ids: Vec<String> = state.nodes.iter().map(|n| n.id.clone()).collect();
    let edges: Vec<(String, String)> = state
        .edges
        .iter()
        .map(|e| (e.source.clone(), e.target.clone()))
        .collect();
    let layout = py.allow_threads(move || layout_graph(&ids, &edges, &options, &sticky));

    state.positions = layout.positions;
    state.layout_width = layout.width;
    state.layout_height = layout.height;
    serde_json::to_string(&state).map_err(|e| err(format!("serialize state: {e}")))
}

#[pyfunction]
pub fn graph_diff(prev_json: String, next_json: String) -> PyResult<String> {
    let prev: GraphState =
        serde_json::from_str(&prev_json).map_err(|e| err(format!("invalid prev state: {e}")))?;
    let next: GraphState =
        serde_json::from_str(&next_json).map_err(|e| err(format!("invalid next state: {e}")))?;
    let diff = diff_states(&prev, &next);
    serde_json::to_string(&diff).map_err(|e| err(format!("serialize diff: {e}")))
}

#[pyfunction]
#[pyo3(signature = (state_json, op, args_json=None))]
pub fn graph_query(
    state_json: String,
    op: String,
    args_json: Option<String>,
) -> PyResult<String> {
    let state: GraphState =
        serde_json::from_str(&state_json).map_err(|e| err(format!("invalid state: {e}")))?;
    let args: serde_json::Value = match args_json.as_deref() {
        Some(raw) if !raw.is_empty() => {
            serde_json::from_str(raw).map_err(|e| err(format!("invalid query args: {e}")))?
        }
        _ => serde_json::json!({}),
    };

    let result = match op.as_str() {
        "hubs" => {
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(15) as usize;
            query_hubs(&state, limit)
        }
        "path" => {
            let from = args
                .get("from")
                .or_else(|| args.get("source"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let to = args
                .get("to")
                .or_else(|| args.get("target"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            query_path(&state, from, to)
        }
        "impact" => {
            let path = args
                .get("path")
                .or_else(|| args.get("id"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let radius = args.get("radius").and_then(|v| v.as_u64()).map(|v| v as usize);
            query_impact(&state, path, radius)
        }
        "cluster" => {
            let id = args
                .get("id")
                .or_else(|| args.get("package"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            query_cluster(&state, id)
        }
        "find" => {
            let query = args.get("query").and_then(|v| v.as_str()).unwrap_or("");
            let kind = args.get("kind").and_then(|v| v.as_str());
            let limit = args.get("limit").and_then(|v| v.as_u64()).unwrap_or(40) as usize;
            query_find(&state, query, kind, limit)
        }
        other => return Err(err(format!("unknown graph query op: {other}"))),
    };

    serde_json::to_string(&result).map_err(|e| err(format!("serialize query result: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_snapshot() -> SnapshotIn {
        SnapshotIn {
            project_path: "/proj".into(),
            generation: 1,
            max_nodes: 6000,
            view: "files".into(),
            files: vec![
                FileIn {
                    id: "a.py".into(),
                    kind: "back".into(),
                    size: 10,
                    symbols: Some(2),
                    role: Some("entry".into()),
                    role_source: Some("manual".into()),
                    role_stale: None,
                },
                FileIn {
                    id: "b.py".into(),
                    kind: "back".into(),
                    size: 20,
                    symbols: None,
                    role: None,
                    role_source: None,
                    role_stale: None,
                },
                FileIn {
                    id: "web/ui.tsx".into(),
                    kind: "front".into(),
                    size: 30,
                    symbols: Some(1),
                    role: None,
                    role_source: None,
                    role_stale: None,
                },
            ],
            edges: vec![["a.py".into(), "b.py".into()], ["web/ui.tsx".into(), "a.py".into()]],
            discovery: "git".into(),
            total_files: 3,
            symbols: 3,
            has_metadata: true,
        }
    }

    #[test]
    fn build_degrees_and_kinds() {
        let state = build_state(sample_snapshot());
        assert_eq!(state.nodes.len(), 3);
        let a = state.nodes.iter().find(|n| n.id == "a.py").unwrap();
        assert_eq!(a.out_degree, 1);
        assert_eq!(a.in_degree, 1);
        assert_eq!(state.kinds.get("back"), Some(&2));
        assert_eq!(state.annotated_manual, 1);
    }

    #[test]
    fn packages_aggregate() {
        let mut snap = sample_snapshot();
        snap.view = "packages".into();
        let state = build_state(snap);
        assert!(state.nodes.iter().any(|n| n.id == "web"));
        assert!(state.nodes.iter().any(|n| n.id == "(root)"));
        assert!(!state.clusters.is_empty());
    }

    #[test]
    fn diff_detects_add_remove() {
        let prev = build_state(sample_snapshot());
        let mut snap = sample_snapshot();
        snap.generation = 2;
        snap.files.pop(); // drop web/ui.tsx
        snap.files.push(FileIn {
            id: "c.py".into(),
            kind: "back".into(),
            size: 5,
            symbols: None,
            role: None,
            role_source: None,
            role_stale: None,
        });
        snap.edges = vec![["a.py".into(), "b.py".into()]];
        let next = build_state(snap);
        let diff = diff_states(&prev, &next);
        assert!(diff.removed_nodes.contains(&"web/ui.tsx".to_string()));
        assert!(diff.added_nodes.iter().any(|n| n.id == "c.py"));
    }

    #[test]
    fn path_and_impact() {
        let state = build_state(sample_snapshot());
        let path = query_path(&state, "web/ui.tsx", "b.py");
        let nodes = path["path"].as_array().unwrap();
        assert_eq!(nodes.len(), 3);
        let impact = query_impact(&state, "a.py", None);
        let deps = impact["dependents"].as_array().unwrap();
        assert!(deps.iter().any(|v| v.as_str() == Some("web/ui.tsx")));
    }

    #[test]
    fn layout_deterministic() {
        let state = build_state(sample_snapshot());
        let ids: Vec<String> = state.nodes.iter().map(|n| n.id.clone()).collect();
        let edges: Vec<(String, String)> = state
            .edges
            .iter()
            .map(|e| (e.source.clone(), e.target.clone()))
            .collect();
        let a = layout_graph(&ids, &edges, &LayoutOptions::default(), &HashMap::new());
        let b = layout_graph(&ids, &edges, &LayoutOptions::default(), &HashMap::new());
        assert_eq!(a.positions, b.positions);
        assert!(a.width > 0.0);
    }
}
