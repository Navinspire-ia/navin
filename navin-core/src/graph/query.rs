//! Graph queries: hubs, shortest path, impact cone, package cluster, find.

use std::collections::{HashMap, HashSet, VecDeque};

use serde_json::{json, Value};

use super::GraphState;

pub fn query_hubs(state: &GraphState, limit: usize) -> Value {
    let mut hubs: Vec<&super::GraphNode> = state
        .nodes
        .iter()
        .filter(|n| n.in_degree + n.out_degree > 0)
        .collect();
    hubs.sort_by(|a, b| {
        let da = a.in_degree + a.out_degree;
        let db = b.in_degree + b.out_degree;
        db.cmp(&da).then_with(|| a.id.cmp(&b.id))
    });
    let items: Vec<Value> = hubs
        .into_iter()
        .take(limit)
        .map(|n| {
            json!({
                "id": n.id,
                "kind": n.kind,
                "in_degree": n.in_degree,
                "out_degree": n.out_degree,
                "role": n.role,
            })
        })
        .collect();
    json!({ "hubs": items })
}

pub fn query_path(state: &GraphState, from: &str, to: &str) -> Value {
    if from.is_empty() || to.is_empty() {
        return json!({ "path": [], "error": "from and to are required" });
    }
    if from == to {
        return json!({ "path": [from], "length": 0 });
    }

    let mut adj: HashMap<&str, Vec<&str>> = HashMap::new();
    for edge in &state.edges {
        adj.entry(edge.source.as_str())
            .or_default()
            .push(edge.target.as_str());
    }

    let mut prev: HashMap<&str, &str> = HashMap::new();
    let mut queue = VecDeque::new();
    queue.push_back(from);
    prev.insert(from, "");

    let mut found = false;
    while let Some(current) = queue.pop_front() {
        if current == to {
            found = true;
            break;
        }
        for next in adj.get(current).into_iter().flatten() {
            if prev.contains_key(next) {
                continue;
            }
            prev.insert(next, current);
            queue.push_back(next);
        }
    }

    if !found {
        return json!({ "path": [], "from": from, "to": to, "found": false });
    }

    let mut path = Vec::new();
    let mut cur = to;
    path.push(cur.to_string());
    while cur != from {
        cur = prev.get(cur).copied().unwrap_or(from);
        path.push(cur.to_string());
        if cur == from {
            break;
        }
    }
    path.reverse();
    let length = path.len().saturating_sub(1);
    json!({ "path": path, "from": from, "to": to, "found": true, "length": length })
}

pub fn query_impact(state: &GraphState, path: &str, radius: Option<usize>) -> Value {
    if path.is_empty() {
        return json!({ "dependents": [], "error": "path is required" });
    }
    let mut reverse: HashMap<&str, Vec<&str>> = HashMap::new();
    for edge in &state.edges {
        reverse
            .entry(edge.target.as_str())
            .or_default()
            .push(edge.source.as_str());
    }

    let max_depth = radius.unwrap_or(usize::MAX);
    let mut seen = HashSet::new();
    let mut order = Vec::new();
    let mut queue = VecDeque::new();
    queue.push_back((path, 0usize));
    seen.insert(path);

    while let Some((current, depth)) = queue.pop_front() {
        if depth > 0 {
            order.push(current.to_string());
        }
        if depth >= max_depth {
            continue;
        }
        for next in reverse.get(current).into_iter().flatten() {
            if seen.insert(next) {
                queue.push_back((next, depth + 1));
            }
        }
    }

    json!({
        "path": path,
        "dependents": order,
        "count": order.len(),
        "radius": radius,
    })
}

pub fn query_cluster(state: &GraphState, id: &str) -> Value {
    if id.is_empty() {
        return json!({ "error": "id is required" });
    }
    if let Some(cluster) = state.clusters.iter().find(|c| c.id == id) {
        return json!({
            "id": cluster.id,
            "label": cluster.label,
            "kind": cluster.kind,
            "member_ids": cluster.member_ids,
            "count": cluster.member_ids.len(),
        });
    }
    // File view: treat package key as dirname prefix.
    let prefix = if id == "(root)" {
        String::new()
    } else {
        format!("{id}/")
    };
    let members: Vec<&str> = state
        .nodes
        .iter()
        .filter(|n| {
            if id == "(root)" {
                !n.id.contains('/')
            } else {
                n.id == id || n.id.starts_with(&prefix)
            }
        })
        .map(|n| n.id.as_str())
        .collect();
    if members.is_empty() {
        return json!({ "id": id, "member_ids": [], "count": 0, "found": false });
    }
    json!({
        "id": id,
        "label": id,
        "member_ids": members,
        "count": members.len(),
        "found": true,
    })
}

pub fn query_find(state: &GraphState, query: &str, kind: Option<&str>, limit: usize) -> Value {
    let q = query.to_lowercase();
    let mut matches: Vec<&super::GraphNode> = state
        .nodes
        .iter()
        .filter(|n| {
            if let Some(k) = kind {
                if n.kind != k {
                    return false;
                }
            }
            if q.is_empty() {
                return kind.is_some();
            }
            n.id.to_lowercase().contains(&q)
                || n.role
                    .as_ref()
                    .map(|r| r.to_lowercase().contains(&q))
                    .unwrap_or(false)
        })
        .collect();
    matches.sort_by(|a, b| {
        let da = a.in_degree + a.out_degree;
        let db = b.in_degree + b.out_degree;
        db.cmp(&da).then_with(|| a.id.cmp(&b.id))
    });
    let items: Vec<Value> = matches
        .into_iter()
        .take(limit)
        .map(|n| {
            json!({
                "id": n.id,
                "kind": n.kind,
                "role": n.role,
                "in_degree": n.in_degree,
                "out_degree": n.out_degree,
            })
        })
        .collect();
    json!({ "matches": items, "count": items.len() })
}
