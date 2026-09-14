//! Runtimes the engine can supervise. The engine itself is Rust-only;
//! these are the runtimes of the projects under test.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Runtime {
    Node,
    Rust,
    Python,
    Go,
    Java,
    Dotnet,
    Ruby,
    Php,
    Unknown,
}

impl Runtime {
    pub fn label(self) -> &'static str {
        match self {
            Runtime::Node => "node",
            Runtime::Rust => "rust",
            Runtime::Python => "python",
            Runtime::Go => "go",
            Runtime::Java => "java",
            Runtime::Dotnet => "dotnet",
            Runtime::Ruby => "ruby",
            Runtime::Php => "php",
            Runtime::Unknown => "unknown",
        }
    }
}
