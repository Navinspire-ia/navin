//! Resolve build/test/start commands per runtime, preferring what the
//! project declares (package.json scripts) over defaults.

use serde_json::Value;
use std::fs;
use std::path::Path;

use super::manifest::LifecycleCommands;
use super::runtime::Runtime;

/// Detect the Node package manager from lockfiles.
pub fn node_package_manager(dir: &Path) -> &'static str {
    if dir.join("pnpm-lock.yaml").is_file() {
        "pnpm"
    } else if dir.join("yarn.lock").is_file() {
        "yarn"
    } else if dir.join("bun.lockb").is_file() || dir.join("bun.lock").is_file() {
        "bun"
    } else {
        "npm"
    }
}

/// Commands from a parsed package.json, using the detected package manager.
/// Without a `start` script, the usual entry files stand in for one.
pub fn node_commands(dir: &Path, pkg: &Value, pm: &str) -> LifecycleCommands {
    let scripts = pkg.get("scripts").and_then(Value::as_object);
    let has = |name: &str| scripts.map(|s| s.contains_key(name)).unwrap_or(false);
    let run = |name: &str| -> Option<String> {
        has(name).then(|| match pm {
            "yarn" => format!("yarn {name}"),
            _ => format!("{pm} run {name}"),
        })
    };
    let entry = pkg
        .get("main")
        .and_then(Value::as_str)
        .filter(|main| dir.join(main).is_file())
        .map(str::to_owned)
        .or_else(|| {
            ["server.js", "index.js", "app.js", "src/index.js", "src/server.js"]
                .into_iter()
                .find(|file| dir.join(file).is_file())
                .map(str::to_owned)
        });
    LifecycleCommands {
        build: run("build"),
        test: run("test"),
        start: run("start").or_else(|| entry.map(|file| format!("node {file}"))),
        dev: run("dev").or_else(|| run("serve")),
    }
}

pub fn default_commands(runtime: Runtime) -> LifecycleCommands {
    match runtime {
        Runtime::Rust => LifecycleCommands {
            build: Some("cargo build".into()),
            test: Some("cargo test".into()),
            start: Some("cargo run".into()),
            dev: None,
        },
        Runtime::Go => LifecycleCommands {
            build: Some("go build ./...".into()),
            test: Some("go test ./...".into()),
            start: Some("go run .".into()),
            dev: None,
        },
        Runtime::Python => LifecycleCommands {
            build: None,
            test: Some("pytest".into()),
            start: None,
            dev: None,
        },
        Runtime::Java => LifecycleCommands {
            build: Some("mvn package".into()),
            test: Some("mvn test".into()),
            start: None,
            dev: None,
        },
        Runtime::Dotnet => LifecycleCommands {
            build: Some("dotnet build".into()),
            test: Some("dotnet test".into()),
            start: Some("dotnet run".into()),
            dev: None,
        },
        _ => LifecycleCommands::default(),
    }
}

/// Python has no universal start command, so it is inferred from files that
/// actually exist. Ports match the ones the dashboard uses for the probe URL.
pub fn python_commands(dir: &Path) -> LifecycleCommands {
    LifecycleCommands {
        build: None,
        test: Some(python_exe(dir, "pytest")),
        start: python_start(dir),
        dev: None,
    }
}

fn python_start(dir: &Path) -> Option<String> {
    if dir.join("manage.py").is_file() {
        return Some(format!("{} manage.py runserver 8000", python_exe(dir, "python")));
    }
    if let Some(module) = python_module_declaring(dir, "FastAPI(") {
        return Some(format!("{} {module}:app --port 8000", python_exe(dir, "uvicorn")));
    }
    if let Some(module) = python_module_declaring(dir, "Flask(") {
        return Some(format!("{} --app {module} run --port 5000", python_exe(dir, "flask")));
    }
    None
}

/// Python commands only work with the project's own dependencies, and a
/// shadow copy has no virtualenv of its own: point at the real one.
fn python_exe(dir: &Path, program: &str) -> String {
    let bin = if cfg!(windows) { "Scripts" } else { "bin" };
    for venv in [".venv", "venv", "env"] {
        for name in [program.to_owned(), format!("{program}.exe")] {
            let candidate = dir.join(venv).join(bin).join(name);
            if candidate.is_file() {
                return candidate.display().to_string();
            }
        }
    }
    program.to_owned()
}

/// Java projects start through their build tool; Spring Boot is the one
/// stack with a universally known run goal.
pub fn java_commands(dir: &Path) -> LifecycleCommands {
    let maven = dir.join("pom.xml").is_file();
    let declares_boot = fs::read_to_string(dir.join(if maven { "pom.xml" } else { "build.gradle" }))
        .unwrap_or_default()
        .contains("spring-boot");
    let start = match (declares_boot, maven) {
        (false, _) => None,
        (true, true) => Some("mvn spring-boot:run".to_owned()),
        (true, false) => Some("./gradlew bootRun".to_owned()),
    };
    let base = if maven {
        default_commands(Runtime::Java)
    } else {
        LifecycleCommands {
            build: Some("./gradlew build".to_owned()),
            test: Some("./gradlew test".to_owned()),
            start: None,
            dev: None,
        }
    };
    LifecycleCommands { start, ..base }
}

/// PHP: Laravel and Symfony ship their own server, everything else is
/// served by the built-in one over the public document root.
pub fn php_commands(dir: &Path) -> LifecycleCommands {
    let start = if dir.join("artisan").is_file() {
        "php artisan serve --port 8000".to_owned()
    } else if dir.join("bin").join("console").is_file() {
        "php -S 127.0.0.1:8000 -t public".to_owned()
    } else {
        let root = if dir.join("public").is_dir() { "public" } else { "." };
        format!("php -S 127.0.0.1:8000 -t {root}")
    };
    let test = ["phpunit.xml", "phpunit.xml.dist"]
        .iter()
        .any(|file| dir.join(file).is_file())
        .then(|| "vendor/bin/phpunit".to_owned());
    LifecycleCommands { build: None, test, start: Some(start), dev: None }
}

/// Ruby: Rails when the app is one, plain Rack otherwise.
pub fn ruby_commands(dir: &Path) -> LifecycleCommands {
    let rails = dir.join("bin").join("rails").is_file();
    let start = if rails {
        Some("bundle exec rails server -p 3000".to_owned())
    } else {
        dir.join("config.ru").is_file().then(|| "bundle exec rackup -p 9292".to_owned())
    };
    let test = if dir.join("spec").is_dir() {
        Some("bundle exec rspec".to_owned())
    } else {
        Some("bundle exec rake test".to_owned())
    };
    LifecycleCommands { build: None, test, start, dev: None }
}

/// Find the module holding the app object, by looking for the constructor
/// call in the usual entry points and in first-level package inits.
fn python_module_declaring(dir: &Path, needle: &str) -> Option<String> {
    let contains = |path: &Path| {
        fs::read_to_string(path).map(|text| text.contains(needle)).unwrap_or(false)
    };
    for candidate in ["main.py", "app.py", "wsgi.py", "asgi.py", "server.py"] {
        if contains(&dir.join(candidate)) {
            return Some(candidate.trim_end_matches(".py").to_owned());
        }
    }
    for parent in ["app", "src", "api"] {
        for candidate in ["main.py", "app.py"] {
            if contains(&dir.join(parent).join(candidate)) {
                return Some(format!("{parent}.{}", candidate.trim_end_matches(".py")));
            }
        }
    }
    let mut packages: Vec<String> = fs::read_dir(dir)
        .ok()?
        .flatten()
        .filter(|entry| entry.path().join("__init__.py").is_file())
        .filter(|entry| contains(&entry.path().join("__init__.py")))
        .filter_map(|entry| entry.file_name().into_string().ok())
        .collect();
    packages.sort();
    packages.into_iter().next()
}
