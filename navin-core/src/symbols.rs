//! Tree-sitter symbol extraction for the code index.
//!
//! One generic engine driven by per-language query strings. Python keeps its
//! stdlib-ast parser (already exact); every other supported language gets a
//! real syntax tree here instead of the regex pattern table. When a language
//! is not covered, or its query fails to compile against the shipped grammar,
//! `ts_extract` returns `None` and Python falls back to the pattern table -
//! the extension can never make indexing worse than it was.

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::sync::Mutex;
use std::sync::OnceLock;

use pyo3::prelude::*;
use serde::Serialize;
use streaming_iterator::StreamingIterator;
use tree_sitter::{Language, Node, Parser, Query, QueryCursor};

const MAX_SYMBOLS: usize = 2000;
const MAX_REFS: usize = 4000;
const SIGNATURE_MAX: usize = 160;

#[derive(Serialize)]
struct SymbolOut {
    name: String,
    kind: String,
    line: usize,
    end_line: usize,
    #[serde(skip_serializing_if = "String::is_empty")]
    qualname: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    signature: String,
    exported: bool,
}

#[derive(Serialize)]
struct ExtractOut {
    symbols: Vec<SymbolOut>,
    imports: Vec<String>,
    /// [name, line, kind, scope] positional rows, matching Reference.to_json.
    refs: Vec<(String, usize, String, String)>,
}

struct LangSpec {
    language: Language,
    query: &'static str,
    /// Node kinds whose `name` field contributes to qualified names.
    containers: &'static [&'static str],
    /// How `exported` is decided for definitions in this language.
    export_rule: ExportRule,
}

#[derive(Clone, Copy)]
enum ExportRule {
    /// Everything is considered exported (C, C++, Java, ...).
    Always,
    /// Go: exported iff the identifier starts with an uppercase letter.
    Capitalized,
    /// JS/TS: exported iff an ancestor is an export statement.
    ExportAncestor,
    /// Rust: exported iff the definition carries a visibility modifier.
    RustPub,
    /// Conventional: a leading underscore means private.
    NotUnderscore,
}

fn spec_for(language: &str, path: &str) -> Option<LangSpec> {
    match language {
        "javascript" => Some(LangSpec {
            language: tree_sitter_javascript::LANGUAGE.into(),
            query: JS_QUERY,
            containers: &["class_declaration"],
            export_rule: ExportRule::ExportAncestor,
        }),
        "typescript" => {
            let lang: Language = if path.ends_with(".tsx") {
                tree_sitter_typescript::LANGUAGE_TSX.into()
            } else {
                tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()
            };
            Some(LangSpec {
                language: lang,
                query: TS_QUERY,
                containers: &["class_declaration", "abstract_class_declaration"],
                export_rule: ExportRule::ExportAncestor,
            })
        }
        "go" => Some(LangSpec {
            language: tree_sitter_go::LANGUAGE.into(),
            query: GO_QUERY,
            containers: &[],
            export_rule: ExportRule::Capitalized,
        }),
        "rust" => Some(LangSpec {
            language: tree_sitter_rust::LANGUAGE.into(),
            query: RUST_QUERY,
            containers: &["mod_item", "trait_item"],
            export_rule: ExportRule::RustPub,
        }),
        "java" => Some(LangSpec {
            language: tree_sitter_java::LANGUAGE.into(),
            query: JAVA_QUERY,
            containers: &["class_declaration", "interface_declaration", "enum_declaration"],
            export_rule: ExportRule::Always,
        }),
        "c" => Some(LangSpec {
            language: tree_sitter_c::LANGUAGE.into(),
            query: C_QUERY,
            containers: &[],
            export_rule: ExportRule::Always,
        }),
        "cpp" => Some(LangSpec {
            language: tree_sitter_cpp::LANGUAGE.into(),
            query: CPP_QUERY,
            containers: &["class_specifier", "struct_specifier", "namespace_definition"],
            export_rule: ExportRule::Always,
        }),
        "csharp" => Some(LangSpec {
            language: tree_sitter_c_sharp::LANGUAGE.into(),
            query: CSHARP_QUERY,
            containers: &["class_declaration", "interface_declaration", "struct_declaration"],
            export_rule: ExportRule::Always,
        }),
        "ruby" => Some(LangSpec {
            language: tree_sitter_ruby::LANGUAGE.into(),
            query: RUBY_QUERY,
            containers: &["class", "module"],
            export_rule: ExportRule::NotUnderscore,
        }),
        "php" => Some(LangSpec {
            language: tree_sitter_php::LANGUAGE_PHP.into(),
            query: PHP_QUERY,
            containers: &["class_declaration", "interface_declaration", "trait_declaration"],
            export_rule: ExportRule::NotUnderscore,
        }),
        "kotlin" => Some(LangSpec {
            language: tree_sitter_kotlin_ng::LANGUAGE.into(),
            query: KOTLIN_QUERY,
            containers: &["class_declaration", "object_declaration", "companion_object"],
            export_rule: ExportRule::NotUnderscore,
        }),
        "swift" => Some(LangSpec {
            language: tree_sitter_swift::LANGUAGE.into(),
            query: SWIFT_QUERY,
            containers: &["class_declaration", "protocol_declaration"],
            export_rule: ExportRule::NotUnderscore,
        }),
        _ => None,
    }
}

// Query capture convention, shared by every language:
//   @def.<kind>  - the whole definition node (kind becomes the symbol kind)
//   @name        - the definition's name, in the same pattern
//   @import      - an import path/statement node
//   @call        - a callee identifier (reference of kind "call")
//   @base        - a superclass/derives identifier (reference of kind "base")

const JS_QUERY: &str = r#"
(function_declaration name: (_) @name) @def.function
(generator_function_declaration name: (_) @name) @def.function
(class_declaration name: (_) @name) @def.class
(method_definition name: (_) @name) @def.method
(variable_declarator name: (identifier) @name value: (arrow_function)) @def.function
(variable_declarator name: (identifier) @name value: (function_expression)) @def.function
(export_statement (lexical_declaration (variable_declarator name: (identifier) @name))) @def.constant
(import_statement source: (string) @import)
(call_expression function: (identifier) @call)
(call_expression function: (member_expression property: (property_identifier) @call))
(new_expression constructor: (identifier) @call)
"#;

const TS_QUERY: &str = r#"
(function_declaration name: (_) @name) @def.function
(generator_function_declaration name: (_) @name) @def.function
(class_declaration name: (_) @name) @def.class
(abstract_class_declaration name: (_) @name) @def.class
(method_definition name: (_) @name) @def.method
(interface_declaration name: (_) @name) @def.interface
(type_alias_declaration name: (_) @name) @def.type
(enum_declaration name: (_) @name) @def.enum
(variable_declarator name: (identifier) @name value: (arrow_function)) @def.function
(variable_declarator name: (identifier) @name value: (function_expression)) @def.function
(export_statement (lexical_declaration (variable_declarator name: (identifier) @name))) @def.constant
(import_statement source: (string) @import)
(call_expression function: (identifier) @call)
(call_expression function: (member_expression property: (property_identifier) @call))
(new_expression constructor: (identifier) @call)
"#;

const GO_QUERY: &str = r#"
(function_declaration name: (_) @name) @def.function
(method_declaration name: (_) @name) @def.method
(type_declaration (type_spec name: (_) @name type: (struct_type))) @def.struct
(type_declaration (type_spec name: (_) @name type: (interface_type))) @def.interface
(const_declaration (const_spec name: (_) @name)) @def.constant
(import_spec path: (interpreted_string_literal) @import)
(call_expression function: (identifier) @call)
(call_expression function: (selector_expression field: (field_identifier) @call))
"#;

const RUST_QUERY: &str = r#"
(function_item name: (_) @name) @def.function
(struct_item name: (_) @name) @def.struct
(enum_item name: (_) @name) @def.enum
(trait_item name: (_) @name) @def.trait
(mod_item name: (_) @name) @def.module
(const_item name: (_) @name) @def.constant
(static_item name: (_) @name) @def.constant
(type_item name: (_) @name) @def.type
(macro_definition name: (_) @name) @def.macro
(use_declaration argument: (_) @import)
(call_expression function: (identifier) @call)
(call_expression function: (field_expression field: (field_identifier) @call))
(call_expression function: (scoped_identifier name: (identifier) @call))
"#;

const JAVA_QUERY: &str = r#"
(class_declaration name: (_) @name) @def.class
(interface_declaration name: (_) @name) @def.interface
(enum_declaration name: (_) @name) @def.enum
(method_declaration name: (_) @name) @def.method
(constructor_declaration name: (_) @name) @def.method
(import_declaration (scoped_identifier) @import)
(method_invocation name: (identifier) @call)
(superclass (type_identifier) @base)
"#;

const C_QUERY: &str = r#"
(function_definition declarator: (function_declarator declarator: (identifier) @name)) @def.function
(struct_specifier name: (type_identifier) @name body: (_)) @def.struct
(enum_specifier name: (type_identifier) @name body: (_)) @def.enum
(type_definition declarator: (type_identifier) @name) @def.type
(preproc_include path: (_) @import)
(call_expression function: (identifier) @call)
"#;

const CPP_QUERY: &str = r#"
(function_definition declarator: (function_declarator declarator: (identifier) @name)) @def.function
(function_definition declarator: (function_declarator declarator: (field_identifier) @name)) @def.method
(function_definition declarator: (function_declarator declarator: (qualified_identifier) @name)) @def.method
(class_specifier name: (type_identifier) @name body: (_)) @def.class
(struct_specifier name: (type_identifier) @name body: (_)) @def.struct
(enum_specifier name: (type_identifier) @name body: (_)) @def.enum
(namespace_definition name: (namespace_identifier) @name) @def.module
(type_definition declarator: (type_identifier) @name) @def.type
(preproc_include path: (_) @import)
(call_expression function: (identifier) @call)
(call_expression function: (field_expression field: (field_identifier) @call))
"#;

const CSHARP_QUERY: &str = r#"
(class_declaration name: (_) @name) @def.class
(interface_declaration name: (_) @name) @def.interface
(struct_declaration name: (_) @name) @def.struct
(enum_declaration name: (_) @name) @def.enum
(method_declaration name: (_) @name) @def.method
(property_declaration name: (_) @name) @def.property
(using_directive (_) @import)
(invocation_expression function: (identifier) @call)
(invocation_expression function: (member_access_expression name: (identifier) @call))
"#;

const RUBY_QUERY: &str = r#"
(method name: (_) @name) @def.method
(singleton_method name: (_) @name) @def.method
(class name: (constant) @name) @def.class
(module name: (constant) @name) @def.module
(call method: (identifier) @call)
"#;

const PHP_QUERY: &str = r#"
(function_definition name: (_) @name) @def.function
(method_declaration name: (_) @name) @def.method
(class_declaration name: (_) @name) @def.class
(interface_declaration name: (_) @name) @def.interface
(trait_declaration name: (_) @name) @def.trait
(namespace_use_declaration (namespace_use_clause (qualified_name) @import))
(function_call_expression function: (name) @call)
(member_call_expression name: (name) @call)
(object_creation_expression (name) @call)
"#;

const KOTLIN_QUERY: &str = r#"
(class_declaration name: (identifier) @name) @def.class
(object_declaration name: (identifier) @name) @def.object
(companion_object name: (identifier) @name) @def.object
(function_declaration name: (identifier) @name) @def.function
(property_declaration (variable_declaration (identifier) @name)) @def.constant
(import (qualified_identifier) @import)
(import (identifier) @import)
(call_expression (identifier) @call)
(call_expression (navigation_expression (identifier) @call))
"#;

const SWIFT_QUERY: &str = r#"
(class_declaration name: (_) @name) @def.class
(protocol_declaration name: (_) @name) @def.protocol
(function_declaration name: (_) @name) @def.function
(init_declaration name: (_) @name) @def.method
(property_declaration name: (pattern (simple_identifier) @name)) @def.constant
(import_declaration (identifier) @import)
(call_expression (simple_identifier) @call)
"#;

/// Compiled query per language name; a language whose query failed to compile
/// is remembered as None so we never retry (and never spam the log).
fn query_cache() -> &'static Mutex<HashMap<String, Option<&'static Query>>> {
    static CACHE: OnceLock<Mutex<HashMap<String, Option<&'static Query>>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn compiled_query(cache_key: &str, spec: &LangSpec) -> Option<&'static Query> {
    let mut cache = query_cache().lock().unwrap();
    if let Some(known) = cache.get(cache_key) {
        return *known;
    }
    let compiled = Query::new(&spec.language, spec.query)
        .ok()
        .map(|q| &*Box::leak(Box::new(q)));
    cache.insert(cache_key.to_string(), compiled);
    compiled
}

/// Reuse one Parser per language per OS thread. Creating a Parser + binding a
/// grammar on every file dominated cold-index cost; the output tree does not
/// borrow the parser, so reuse cannot change extraction results.
fn parse_source(cache_key: &str, language: &Language, source: &str) -> Option<tree_sitter::Tree> {
    thread_local! {
        static PARSERS: RefCell<HashMap<String, Parser>> = RefCell::new(HashMap::new());
    }
    PARSERS.with(|cell| {
        let mut parsers = cell.borrow_mut();
        let parser = parsers.entry(cache_key.to_string()).or_insert_with(Parser::new);
        parser.set_language(language).ok()?;
        parser.parse(source, None)
    })
}

fn node_text<'a>(node: Node<'_>, source: &'a str) -> &'a str {
    source.get(node.start_byte()..node.end_byte()).unwrap_or("")
}

fn strip_quotes(raw: &str) -> String {
    raw.trim_matches(|c| c == '"' || c == '\'' || c == '`' || c == '<' || c == '>')
        .to_string()
}

/// Qualified name from enclosing containers (class/module/impl blocks).
fn qualified_name(node: Node<'_>, source: &str, containers: &[&str], name: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut current = node.parent();
    while let Some(parent) = current {
        let kind = parent.kind();
        if containers.contains(&kind) {
            if let Some(name_node) = parent.child_by_field_name("name") {
                parts.push(node_text(name_node, source).to_string());
            }
        } else if kind == "impl_item" {
            // Rust impl blocks: qualify methods by the implemented type.
            if let Some(type_node) = parent.child_by_field_name("type") {
                parts.push(node_text(type_node, source).to_string());
            }
        }
        current = parent.parent();
    }
    if parts.is_empty() {
        return String::new();
    }
    parts.reverse();
    parts.push(name.to_string());
    parts.join(".")
}

fn is_exported(rule: ExportRule, def_node: Node<'_>, name: &str) -> bool {
    match rule {
        ExportRule::Always => true,
        ExportRule::Capitalized => name.chars().next().is_some_and(|c| c.is_uppercase()),
        ExportRule::NotUnderscore => !name.starts_with('_'),
        ExportRule::ExportAncestor => {
            let mut current = def_node.parent();
            while let Some(parent) = current {
                if parent.kind().starts_with("export") {
                    return true;
                }
                current = parent.parent();
            }
            false
        }
        ExportRule::RustPub => {
            let mut cursor = def_node.walk();
            let has_vis = def_node
                .children(&mut cursor)
                .any(|child| child.kind() == "visibility_modifier");
            has_vis
        }
    }
}

fn signature_for(def_node: Node<'_>, source: &str, name: &str) -> String {
    let Some(params) = def_node.child_by_field_name("parameters") else {
        return String::new();
    };
    let params_text: String = node_text(params, source)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let mut signature = format!("{name}{params_text}");
    // Byte truncate can land mid-codepoint; PanicException then kills the
    // whole Python turn (metagraph runtime context) with no UI recovery.
    let mut end = SIGNATURE_MAX.min(signature.len());
    while end > 0 && !signature.is_char_boundary(end) {
        end -= 1;
    }
    signature.truncate(end);
    signature
}

/// Innermost definition whose range encloses `byte`, used to attribute a
/// reference to its enclosing function/method for the call graph.
fn enclosing_scope(defs: &[(usize, usize, String)], byte: usize) -> String {
    let mut best: Option<&(usize, usize, String)> = None;
    for def in defs {
        if def.0 <= byte && byte < def.1 {
            best = match best {
                Some(current) if current.1 - current.0 <= def.1 - def.0 => Some(current),
                _ => Some(def),
            };
        }
    }
    best.map(|d| d.2.clone()).unwrap_or_default()
}

fn extract_impl(language: &str, path: &str, source: &str) -> Option<String> {
    let spec = spec_for(language, path)?;
    let cache_key = if language == "typescript" && path.ends_with(".tsx") {
        "typescript-tsx"
    } else {
        language
    };
    let query = compiled_query(cache_key, &spec)?;

    let tree = parse_source(cache_key, &spec.language, source)?;
    let root = tree.root_node();

    let capture_names = query.capture_names();
    let mut symbols: Vec<SymbolOut> = Vec::new();
    let mut symbol_index: HashMap<(String, usize), usize> = HashMap::new();
    let mut imports: Vec<String> = Vec::new();
    let mut import_seen: HashSet<String> = HashSet::new();
    // (start_byte, end_byte, qualname) of every definition, for scope lookup.
    let mut def_ranges: Vec<(usize, usize, String)> = Vec::new();
    // Raw reference captures, resolved to scopes after all defs are known.
    let mut raw_refs: Vec<(String, usize, String, usize)> = Vec::new();

    let mut cursor = QueryCursor::new();
    let mut matches = cursor.matches(query, root, source.as_bytes());
    while let Some(m) = matches.next() {
        let mut def_node: Option<(Node, &str)> = None;
        let mut name_node: Option<Node> = None;
        for capture in m.captures {
            let capture_name = capture_names[capture.index as usize];
            if let Some(kind) = capture_name.strip_prefix("def.") {
                def_node = Some((capture.node, kind));
            } else if capture_name == "name" {
                name_node = Some(capture.node);
            } else if capture_name == "import" {
                let text = strip_quotes(node_text(capture.node, source).trim());
                // Preserve first-seen order; HashSet only speeds the dedupe.
                if !text.is_empty() && import_seen.insert(text.clone()) {
                    imports.push(text);
                }
            } else if capture_name == "call" || capture_name == "base" {
                if raw_refs.len() < MAX_REFS {
                    let node = capture.node;
                    raw_refs.push((
                        node_text(node, source).to_string(),
                        node.start_position().row + 1,
                        capture_name.to_string(),
                        node.start_byte(),
                    ));
                }
            }
        }
        if let (Some((node, kind)), Some(name)) = (def_node, name_node) {
            if symbols.len() >= MAX_SYMBOLS {
                continue;
            }
            let name_text = node_text(name, source).to_string();
            if name_text.is_empty() {
                continue;
            }
            let line = name.start_position().row + 1;
            // An `export const f = () => {}` matches both the function and the
            // exported-constant pattern; the more specific kind wins.
            if let Some(index) = symbol_index.get(&(name_text.clone(), line)) {
                if kind == "constant" {
                    continue;
                }
                let existing: &mut SymbolOut = &mut symbols[*index];
                if existing.kind == "constant" {
                    existing.kind = kind.to_string();
                    existing.signature = signature_for(node, source, &name_text);
                }
                continue;
            }
            let qualname = qualified_name(node, source, spec.containers, &name_text);
            def_ranges.push((
                node.start_byte(),
                node.end_byte(),
                if qualname.is_empty() { name_text.clone() } else { qualname.clone() },
            ));
            symbol_index.insert((name_text.clone(), line), symbols.len());
            symbols.push(SymbolOut {
                name: name_text.clone(),
                kind: kind.to_string(),
                line: node.start_position().row + 1,
                end_line: node.end_position().row + 1,
                qualname,
                signature: signature_for(node, source, &name_text),
                exported: is_exported(spec.export_rule, node, &name_text),
            });
        }
    }

    let refs = raw_refs
        .into_iter()
        .map(|(name, line, kind, byte)| {
            (name, line, kind, enclosing_scope(&def_ranges, byte))
        })
        .collect();

    let out = ExtractOut { symbols, imports, refs };
    serde_json::to_string(&out).ok()
}

/// Extract symbols, imports and references with tree-sitter.
///
/// Returns a JSON string (see `ExtractOut`) or `None` when the language is
/// not covered - the caller then falls back to its pattern table.
#[pyfunction]
#[pyo3(signature = (language, path, source))]
pub fn ts_extract(
    py: Python<'_>,
    language: &str,
    path: &str,
    source: &str,
) -> Option<String> {
    let language = language.to_string();
    let path = path.to_string();
    let source = source.to_string();
    py.allow_threads(move || extract_impl(&language, &path, &source))
}
