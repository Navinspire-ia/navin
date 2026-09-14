//! Multi-service topology from docker-compose files.

use serde_yaml::Value;
use std::fs;
use std::path::Path;

use super::manifest::{Service, ServiceKind};

const COMPOSE_FILES: &[&str] = &[
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
];

/// Parse the first compose file found at the root, best effort: a broken
/// compose file must not break discovery.
pub fn discover_services(root: &Path) -> Vec<Service> {
    for name in COMPOSE_FILES {
        let path = root.join(name);
        if !path.is_file() {
            continue;
        }
        match fs::read_to_string(&path) {
            Ok(text) => return parse_compose(&text),
            Err(_) => return Vec::new(),
        }
    }
    Vec::new()
}

fn parse_compose(text: &str) -> Vec<Service> {
    let doc: Value = match serde_yaml::from_str(text) {
        Ok(doc) => doc,
        Err(_) => return Vec::new(),
    };
    let Some(services) = doc.get("services").and_then(Value::as_mapping) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (name, body) in services {
        let Some(name) = name.as_str() else { continue };
        let image = body
            .get("image")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let ports = body
            .get("ports")
            .and_then(Value::as_sequence)
            .map(|seq| seq.iter().filter_map(host_port).collect())
            .unwrap_or_default();
        let kind = classify(name, image.as_deref());
        out.push(Service {
            name: name.to_owned(),
            image,
            ports,
            kind,
        });
    }
    out
}

/// `"8080:80"` → 8080; `8080` → 8080; long-form mappings use `published`.
fn host_port(entry: &Value) -> Option<u16> {
    if let Some(n) = entry.as_u64() {
        return u16::try_from(n).ok();
    }
    if let Some(s) = entry.as_str() {
        let first = s.split(':').next()?;
        return first.trim().parse().ok();
    }
    if let Some(published) = entry.get("published") {
        if let Some(n) = published.as_u64() {
            return u16::try_from(n).ok();
        }
        if let Some(s) = published.as_str() {
            return s.trim().parse().ok();
        }
    }
    None
}

fn classify(name: &str, image: Option<&str>) -> ServiceKind {
    let hay = format!("{} {}", name, image.unwrap_or("")).to_lowercase();
    const DATABASES: &[&str] = &["postgres", "mysql", "mariadb", "mongo", "sqlite", "mssql", "cockroach"];
    const CACHES: &[&str] = &["redis", "memcached", "valkey"];
    const QUEUES: &[&str] = &["rabbitmq", "kafka", "nats", "sqs", "celery"];
    if DATABASES.iter().any(|m| hay.contains(m)) {
        ServiceKind::Database
    } else if CACHES.iter().any(|m| hay.contains(m)) {
        ServiceKind::Cache
    } else if QUEUES.iter().any(|m| hay.contains(m)) {
        ServiceKind::Queue
    } else {
        ServiceKind::App
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compose_services_are_parsed_and_classified() {
        let text = r#"
services:
  api:
    build: .
    ports: ["8080:80"]
  db:
    image: postgres:16
    ports:
      - "5432:5432"
  cache:
    image: redis:7
"#;
        let services = parse_compose(text);
        assert_eq!(services.len(), 3);
        let db = services.iter().find(|s| s.name == "db").unwrap();
        assert_eq!(db.kind, ServiceKind::Database);
        assert_eq!(db.ports, vec![5432]);
        let api = services.iter().find(|s| s.name == "api").unwrap();
        assert_eq!(api.ports, vec![8080]);
        assert_eq!(api.kind, ServiceKind::App);
    }

    #[test]
    fn broken_compose_yields_no_services() {
        assert!(parse_compose("{{{ not yaml").is_empty());
    }
}
