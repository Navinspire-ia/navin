"""Source repository reverse: clone, inventory, rebuild plan, batched filing, parity.

A binary is reversed from its bytes; an open source project (Odoo, Supabase,
any GitHub repository) is reversed from its tree. This module turns a repo
into an exhaustive, dependency-ordered rebuild plan that the agent can work
through from A to Z without a size limit:

- ``inventory`` clones the repo (shallow, cached outside the workspace) and
  lists every component (Odoo addon, npm/Cargo/Go/Python/Elixir package...)
  with its internal dependencies, files, tests and license.
- ``plan`` orders the components in waves (dependencies first) and splits
  big ones into chunks, in ``.navin/reverse/<id>/rebuild-plan.json``.
- ``next-batch`` files the next ready steps on the project board, a bounded
  batch at a time, and archives finished ones so a plan of thousands of
  steps never overflows the board.
- ``parity`` compares the original tree with the rebuild: the rebuild is only
  finished when every component is covered.

Usage::

    python -m navin.reverse.repo inventory <github-url|owner/name[@ref]|folder>... [--ref REF] [--id ID]
    python -m navin.reverse.repo plan <id> [--max-files 60]
    python -m navin.reverse.repo next-batch <id> [--size 30]
    python -m navin.reverse.repo parity <id> [--rebuild DIR] [--threshold 1.0]
    python -m navin.reverse.repo status <id>
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from navin.reverse.data_schema import extract_data_schema

_SKIP_DIRS = frozenset({
    ".git", "node_modules", "vendor", "dist", "build", ".next", "target",
    "__pycache__", ".venv", "venv", "coverage", ".turbo", ".cache", ".navin",
    "_build", "deps", ".dart_tool", ".gradle", ".idea", ".vscode",
})
_LANG = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go",
    ".rs": "rust", ".java": "java", ".kt": "kotlin", ".scala": "scala", ".rb": "ruby",
    ".php": "php", ".cs": "csharp", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".hpp": "cpp", ".swift": "swift", ".ex": "elixir", ".exs": "elixir", ".erl": "erlang",
    ".hs": "haskell", ".dart": "dart", ".lua": "lua", ".sh": "shell", ".sql": "sql",
    ".vue": "vue", ".svelte": "svelte", ".scss": "scss", ".css": "css", ".less": "less",
    ".html": "html", ".xml": "xml", ".proto": "protobuf", ".graphql": "graphql",
}
_DATA_EXT = frozenset({".json", ".yaml", ".yml", ".toml", ".csv", ".po", ".pot", ".ini", ".env"})
_DOC_EXT = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
_MANIFESTS = {
    "__manifest__.py": "odoo", "__openerp__.py": "odoo", "package.json": "npm",
    "pyproject.toml": "python", "setup.py": "python", "go.mod": "go", "Cargo.toml": "rust",
    "composer.json": "php", "pom.xml": "maven", "build.gradle": "gradle",
    "build.gradle.kts": "gradle", "Gemfile": "ruby", "mix.exs": "elixir",
    "package.yaml": "haskell", "deno.json": "deno", "pubspec.yaml": "dart",
}
_TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)(/|$)|(_test|\.test|\.spec|_spec)\.\w+$|(^|/)test_[^/]+$")
_MIGRATION_RE = re.compile(r"(^|/)(migrations?|migrate|schema)(/|$)")
_MAX_LINE_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------- targets ---

def parse_target(target: str) -> dict[str, Any]:
    """Return {kind: local|git, url, owner, name, ref} for a repo target."""
    raw = target.strip()
    path = Path(raw).expanduser()
    if path.is_dir():
        return {"kind": "local", "path": str(path.resolve()), "name": path.resolve().name,
                "owner": None, "ref": None, "url": None}
    m = re.match(r"^(?:https?://|git@)([^/:]+)[/:]([^/]+)/([^/#?]+?)(?:\.git)?(?:/(?:tree|-/tree)/([^?#]+))?/?$", raw)
    if m:
        host, owner, name, ref = m.groups()
        return {"kind": "git", "url": f"https://{host}/{owner}/{name}.git", "host": host,
                "owner": owner, "name": name, "ref": ref}
    m = re.match(r"^([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*?)(?:\.git)?(?:@([\w./-]+))?$", raw)
    if m:
        owner, name, ref = m.groups()
        return {"kind": "git", "url": f"https://github.com/{owner}/{name}.git", "host": "github.com",
                "owner": owner, "name": name, "ref": ref}
    raise ValueError(f"not a repository URL, owner/name or folder: {target}")


def repos_cache() -> Path:
    return Path(os.environ.get("NAVIN_REVERSE_REPOS") or Path.home() / ".navin" / "reverse" / "repos")


def _git(*args: str, cwd: Path | None = None, timeout: int = 3600) -> str:
    from navin.utils.proc import no_window_kwargs

    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, **no_window_kwargs())
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {(proc.stderr or proc.stdout).strip()[-400:]}")
    return proc.stdout.strip()


def fetch_source(spec: dict[str, Any], *, ref: str | None = None, refresh: bool = False) -> tuple[Path, str | None]:
    """Shallow-clone a git target into the cache (or reuse it). Returns (root, commit)."""
    if spec["kind"] == "local":
        root = Path(spec["path"])
        try:
            return root, _git("rev-parse", "HEAD", cwd=root, timeout=30)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            return root, None
    ref = ref or spec.get("ref")
    dest = repos_cache() / f"{spec['owner']}__{spec['name']}" / (re.sub(r"[^\w.-]", "_", ref) if ref else "default")
    if (dest / ".git").is_dir() and refresh:
        _git("fetch", "--depth", "1", "origin", ref or "HEAD", cwd=dest)
        _git("reset", "--hard", "FETCH_HEAD", cwd=dest)
    elif not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--depth", "1", "--single-branch"]
        if ref:
            args += ["--branch", ref]
        _git(*args, spec["url"], str(dest))
    return dest, _git("rev-parse", "HEAD", cwd=dest, timeout=30)


# -------------------------------------------------------------- inventory ---

def _license(root: Path) -> dict[str, Any]:
    for cand in sorted(root.iterdir()) if root.is_dir() else []:
        if cand.is_file() and re.match(r"^(LICEN[CS]E|COPYING)", cand.name, re.I):
            text = cand.read_text(encoding="utf-8", errors="replace")[:20000]
            checks = (
                ("GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0"),
                ("GNU LESSER GENERAL PUBLIC LICENSE", "LGPL-3.0" if "Version 3" in text else "LGPL-2.1"),
                ("GNU GENERAL PUBLIC LICENSE", "GPL-3.0" if "Version 3" in text else "GPL-2.0"),
                ("Apache License", "Apache-2.0"), ("Mozilla Public License", "MPL-2.0"),
                ("Business Source License", "BUSL-1.1"), ("Elastic License", "Elastic-2.0"),
                ("Permission is hereby granted, free of charge", "MIT"),
                ("Redistribution and use in source and binary forms", "BSD"),
            )
            spdx = next((s for needle, s in checks if needle.lower() in text.lower()), "unknown")
            copyleft = spdx.split("-")[0] in {"AGPL", "GPL", "LGPL", "MPL"}
            source_available = spdx in {"BUSL-1.1", "Elastic-2.0"}
            return {"spdx": spdx, "file": cand.name, "copyleft": copyleft,
                    "source_available_only": source_available}
    return {"spdx": "none", "file": None, "copyleft": False, "source_available_only": False}


def _read_manifest(path: Path, eco: str) -> dict[str, Any]:
    """Name, version, license and declared dependency names of one manifest."""
    info: dict[str, Any] = {"name": path.parent.name, "version": None, "license": None,
                            "depends": [], "workspace": False}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if eco == "odoo":
            node = next((n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Dict)), None)
            data = ast.literal_eval(node) if node is not None else {}
            if isinstance(data, dict):
                info.update(version=data.get("version"), license=data.get("license"),
                            depends=[str(d) for d in data.get("depends") or []], title=data.get("name"))
        elif eco == "npm":
            data = json.loads(text)
            info.update(name=data.get("name") or info["name"], version=data.get("version"),
                        license=data.get("license"), workspace=bool(data.get("workspaces")),
                        depends=sorted({*(data.get("dependencies") or {}), *(data.get("devDependencies") or {}),
                                        *(data.get("peerDependencies") or {})}))
        elif eco == "python" and path.name == "pyproject.toml":
            data = tomllib.loads(text)
            proj = data.get("project") or data.get("tool", {}).get("poetry") or {}
            deps = proj.get("dependencies") or []
            names = deps if isinstance(deps, list) else list(deps)
            info.update(name=proj.get("name") or info["name"], version=proj.get("version"),
                        depends=sorted({re.split(r"[\s<>=!~;\[(]", str(d), maxsplit=1)[0].lower() for d in names if d}))
        elif eco == "rust":
            data = tomllib.loads(text)
            pkg = data.get("package") or {}
            info.update(name=pkg.get("name") or info["name"], version=pkg.get("version")
                        if isinstance(pkg.get("version"), str) else None,
                        workspace="workspace" in data and not pkg,
                        depends=sorted({*(data.get("dependencies") or {}), *(data.get("dev-dependencies") or {})}))
        elif eco == "go":
            mod = re.search(r"^module\s+(\S+)", text, re.M)
            info.update(name=mod.group(1) if mod else info["name"],
                        depends=sorted(set(re.findall(r"^\s*(?:require\s+)?([\w.-]+\.[\w./-]+)\s+v[\w.+-]+", text, re.M))))
        elif eco == "elixir":
            app = re.search(r"app:\s*:(\w+)", text)
            info.update(name=app.group(1) if app else info["name"], depends=sorted(set(re.findall(r"\{:(\w+),", text))))
        elif eco == "php":
            data = json.loads(text)
            info.update(name=data.get("name") or info["name"], license=data.get("license"),
                        depends=sorted(data.get("require") or {}))
    except (ValueError, SyntaxError, TypeError, OSError, tomllib.TOMLDecodeError):
        info["parse_error"] = True
    return info


def _kind(rel: str) -> str:
    ext = os.path.splitext(rel)[1].lower()
    if ext in _LANG:
        return "source"
    if ext in _DATA_EXT:
        return "data"
    if ext in _DOC_EXT:
        return "doc"
    return "asset"


def _walk(root: Path) -> list[str]:
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        base = Path(dirpath).relative_to(root).as_posix()
        for name in sorted(filenames):
            out.append(name if base == "." else f"{base}/{name}")
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "root"


def build_repo_inventory(root: Path, *, name: str | None = None) -> dict[str, Any]:
    """Exhaustive inventory of a source tree: components, files, deps, license.

    *name* is the repository name; the clone folder is named after the ref.
    """
    root = Path(root)
    repo_name = name or root.name
    files = _walk(root)
    comp_dirs: dict[str, dict[str, Any]] = {}
    for rel in files:
        name = rel.rsplit("/", 1)[-1]
        eco = _MANIFESTS.get(name)
        if eco is None or (name == "setup.py" and (root / rel).with_name("pyproject.toml").exists()):
            continue
        cdir = rel.rsplit("/", 1)[0] if "/" in rel else "."
        if cdir in comp_dirs and eco != "odoo":  # an Odoo manifest names the addon, others are extras
            comp_dirs[cdir].setdefault("also", []).append(eco)
            continue
        comp_dirs[cdir] = {"ecosystem": eco, **_read_manifest(root / rel, eco)}
    if "." not in comp_dirs:
        comp_dirs["."] = {"ecosystem": "root", "name": repo_name, "version": None,
                          "license": None, "depends": [], "workspace": True}
    elif comp_dirs["."]["name"] == root.name:
        comp_dirs["."]["name"] = repo_name
    def owner_of(rel: str) -> str:
        # Walk up the parents: O(depth) per file, so 100k files x 1k addons stays fast.
        parts = rel.split("/")[:-1]
        for n in range(len(parts), 0, -1):
            d = "/".join(parts[:n])
            if d in comp_dirs:
                return d
        return "."

    per: dict[str, list[str]] = defaultdict(list)
    for rel in files:
        per[owner_of(rel)].append(rel)
    # A name declared twice (a vendored copy deep in a tree) resolves to the shallowest one.
    name_to_dir: dict[str, str] = {}
    for d in sorted((x for x in comp_dirs if x != "."), key=lambda x: (-x.count("/"), x)):
        name_to_dir[comp_dirs[d]["name"]] = d
    langs_total: Counter[str] = Counter()
    components: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for d in sorted(comp_dirs, key=lambda x: (x != ".", x)):
        meta = comp_dirs[d]
        cfiles = per.get(d, [])
        langs: Counter[str] = Counter()
        lines = 0
        for rel in cfiles:
            lang = _LANG.get(os.path.splitext(rel)[1].lower())
            if lang:
                langs[lang] += 1
                try:
                    p = root / rel
                    if p.stat().st_size <= _MAX_LINE_BYTES:
                        lines += p.read_bytes().count(b"\n")
                except OSError:
                    pass
        langs_total.update(langs)
        cid = _slug(meta["name"] if d != "." else "root")
        while cid in used_ids:
            cid = f"{cid}-{len(used_ids)}"
        used_ids.add(cid)
        internal = sorted({name_to_dir[n] for n in meta.get("depends", []) if n in name_to_dir and name_to_dir[n] != d})
        components.append({
            "id": cid, "path": d, "name": meta["name"], "ecosystem": meta["ecosystem"],
            "root": d == ".", "prefix": "", "source_root": str(root),
            "version": meta.get("version"), "license": meta.get("license"),
            "internal_depends": internal,
            "external_depends": sorted(n for n in meta.get("depends", []) if n not in name_to_dir),
            "files": cfiles,
            "counts": {
                "files": len(cfiles), "lines": lines,
                "source": sum(1 for f in cfiles if _kind(f) == "source"),
                "data": sum(1 for f in cfiles if _kind(f) == "data"),
                "assets": sum(1 for f in cfiles if _kind(f) == "asset"),
                "tests": sum(1 for f in cfiles if _TEST_RE.search(f)),
                "migrations": sum(1 for f in cfiles if _MIGRATION_RE.search(f) or f.endswith(".sql")),
            },
            "languages": dict(langs.most_common()),
        })
    dir_to_id = {c["path"]: c["id"] for c in components}
    for c in components:
        c["internal_depends"] = [dir_to_id[p] for p in c["internal_depends"]]
    compose = [f for f in files if re.search(r"(^|/)(docker-)?compose[\w.-]*\.ya?ml$", f)]
    services: list[str] = []
    for f in compose:
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
            block = re.search(r"^services:\s*\n((?:[ \t]+.*\n?|\s*\n)+)", text, re.M)
            if block:
                services += re.findall(r"^  ([\w.-]+):", block.group(1), re.M)
        except OSError:
            pass
    return {
        "kind": "repository",
        "license": _license(root),
        "languages": dict(langs_total.most_common()),
        "components": components,
        "compose_files": compose,
        "compose_services": sorted(set(services)),
        "data_schema": extract_data_schema(root, files),
        "counts": {
            "files": len(files), "components": len(components),
            "source": sum(c["counts"]["source"] for c in components),
            "tests": sum(c["counts"]["tests"] for c in components),
            "migrations": sum(c["counts"]["migrations"] for c in components),
            "lines": sum(c["counts"]["lines"] for c in components),
        },
    }


# ------------------------------------------------------------ multi repo ---

def merge_inventories(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """One product from several repos (Supabase: auth, storage, studio...).

    Each part is ``{"prefix": folder, "inventory": inv}``: the repo is rebuilt
    under ``<prefix>/`` and a dependency on a package that another repo of the
    product publishes becomes an internal dependency, so waves order them.
    """
    components: list[dict[str, Any]] = []
    used: set[str] = set()
    langs: Counter[str] = Counter()
    licenses: dict[str, Any] = {}
    compose: list[str] = []
    services: set[str] = set()
    for part in parts:
        prefix, inv = part["prefix"], part["inventory"]
        licenses[prefix] = inv["license"]
        langs.update(inv.get("languages") or {})
        compose += [f"{prefix}/{f}" for f in inv.get("compose_files") or []]
        services.update(inv.get("compose_services") or [])
        ids: dict[str, str] = {}
        for comp in inv["components"]:
            new_id = f"{_slug(prefix)}-{comp['id']}"
            while new_id in used:
                new_id = f"{new_id}-{len(used)}"
            used.add(new_id)
            ids[comp["id"]] = new_id
        for comp in inv["components"]:
            components.append({
                **comp,
                "id": ids[comp["id"]],
                "path": prefix if comp["path"] == "." else f"{prefix}/{comp['path']}",
                "root": comp.get("root", comp["path"] == "."),
                "prefix": prefix,
                "files": [f"{prefix}/{f}" for f in comp["files"]],
                "internal_depends": [ids[d] for d in comp["internal_depends"] if d in ids],
            })
    by_name = {c["name"]: c for c in components if c["ecosystem"] != "root"}
    for comp in components:
        linked = [n for n in comp["external_depends"]
                  if n in by_name and by_name[n]["prefix"] != comp["prefix"]]
        if linked:
            comp["internal_depends"] = sorted({*comp["internal_depends"], *(by_name[n]["id"] for n in linked)})
            comp["external_depends"] = [n for n in comp["external_depends"] if n not in linked]
    spdx = sorted({lic["spdx"] for lic in licenses.values()})
    return {
        "kind": "product",
        "license": {
            "spdx": spdx[0] if len(spdx) == 1 else "mixed",
            "copyleft": any(lic["copyleft"] for lic in licenses.values()),
            "source_available_only": any(lic["source_available_only"] for lic in licenses.values()),
            "per_repo": licenses,
        },
        "languages": dict(langs.most_common()),
        "components": components,
        "compose_files": compose,
        "compose_services": sorted(services),
        "counts": {
            "files": sum(len(c["files"]) for c in components), "components": len(components),
            "repos": len(parts),
            **{k: sum(c["counts"][k] for c in components) for k in ("source", "tests", "migrations", "lines")},
        },
    }


# ------------------------------------------------------------------- plan ---

def _is_root(component: dict[str, Any]) -> bool:
    return bool(component.get("root", component["path"] == "."))


def _effective_deps(components: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Dependencies the plan orders on. A repo root (tooling, CI, shared config)
    comes before every part of its repo and never waits for one of them;
    between repos, a root waits for the repos it uses."""
    prefix_of = {c["id"]: c.get("prefix", "") for c in components}
    root_of = {c.get("prefix", ""): c["id"] for c in components if _is_root(c)}
    deps: dict[str, set[str]] = {}
    for c in components:
        wanted = set(c["internal_depends"]) - {c["id"]}
        if _is_root(c):
            wanted = {d for d in wanted if prefix_of.get(d) != prefix_of[c["id"]]}
        elif root_of.get(prefix_of[c["id"]]):
            wanted.add(root_of[prefix_of[c["id"]]])
        deps[c["id"]] = wanted & set(prefix_of)
    return deps


def _waves(components: list[dict[str, Any]]) -> list[list[str]]:
    """Topological waves over :func:`_effective_deps`."""
    deps = _effective_deps(components)
    done: set[str] = set()
    waves: list[list[str]] = []
    remaining = set(deps)
    while remaining:
        wave = sorted(c for c in remaining if deps[c] <= done)
        if not wave:  # dependency cycle: release the least blocked, the cycle is reported
            fewest = min(len(deps[c] - done) for c in remaining)
            wave = sorted(c for c in remaining if len(deps[c] - done) == fewest)
        waves.append(wave)
        done |= set(wave)
        remaining -= set(wave)
    return waves


def _chunks(component: dict[str, Any], max_files: int) -> list[tuple[str, list[str]]]:
    work = [f for f in component["files"] if _kind(f) != "doc" or f.rsplit("/", 1)[-1].upper().startswith("README")]
    if len(work) <= max_files:
        return [("", work)]
    prefix = "" if component["path"] == "." else component["path"] + "/"
    groups: dict[str, list[str]] = defaultdict(list)
    for f in work:
        rest = f[len(prefix):]
        groups[rest.split("/", 1)[0] if "/" in rest else "(top)"].append(f)
    out: list[tuple[str, list[str]]] = []
    for group in sorted(groups):
        items = groups[group]
        for i in range(0, len(items), max_files):
            part = items[i:i + max_files]
            label = group if len(items) <= max_files else f"{group} {i // max_files + 1}/{-(-len(items) // max_files)}"
            out.append((label, part))
    return out


def build_plan(inventory: dict[str, Any], *, source_root: str, max_files: int = 60) -> dict[str, Any]:
    """Dependency-ordered steps covering every file of every component."""
    comps = {c["id"]: c for c in inventory["components"]}
    root_of = {c.get("prefix", ""): c["id"] for c in inventory["components"] if _is_root(c)}
    waves = _waves(inventory["components"])
    steps: list[dict[str, Any]] = []
    final_of: dict[str, str] = {}
    for wave_no, wave in enumerate(waves):
        for cid in wave:
            comp = comps[cid]
            dep_finals = [final_of[d] for d in comp["internal_depends"] if d in final_of]
            own_root = root_of.get(comp.get("prefix", ""))
            if not _is_root(comp) and own_root in final_of:
                dep_finals = sorted({*dep_finals, final_of[own_root]})
            origin = comp.get("source_root") or source_root
            prefix = comp.get("prefix", "")
            chunks = _chunks(comp, max_files)
            where = comp["path"]
            chunk_ids: list[str] = []
            for label, part in chunks:
                sid = f"s{len(steps) + 1:05d}"
                title = f"Rebuild {comp['name']}" + (f" - {label}" if label else "") + f" ({where})"
                steps.append({
                    "id": sid, "title": title[:200], "component": cid, "wave": wave_no,
                    "depends_on": dep_finals, "files": part,
                    "source_root": origin, "prefix": prefix,
                    "acceptance": (f"Every file of step {sid} has a rebuilt equivalent at the same path, "
                                   f"with the behaviour of the original read from {origin}; "
                                   "its tests and the project build pass."),
                })
                chunk_ids.append(sid)
            if len(chunk_ids) > 1:
                sid = f"s{len(steps) + 1:05d}"
                steps.append({
                    "id": sid, "title": f"Integrate and test {comp['name']} ({where})"[:200],
                    "component": cid, "wave": wave_no, "depends_on": chunk_ids, "files": [],
                    "source_root": origin, "prefix": prefix,
                    "acceptance": (f"`python -m navin.reverse.repo parity <id> --component {cid}` reports 100% "
                                   f"and the {comp['counts']['tests']} original test file(s) are ported and green."),
                })
                final_of[cid] = sid
            else:
                final_of[cid] = chunk_ids[0]
    position = {cid: n for n, wave in enumerate(waves) for cid in wave}
    effective = _effective_deps(inventory["components"])
    cycles = [cid for cid, wanted in effective.items()
              if any(position[d] >= position[cid] for d in wanted)]
    return {"source_root": source_root, "max_files": max_files, "waves": waves,
            "steps": steps, "cycles": sorted(set(cycles)),
            "counts": {"steps": len(steps), "waves": len(waves), "components": len(comps)}}


# ------------------------------------------------------------ persistence ---

def plan_dir(project: Path | str, plan_id: str) -> Path:
    return Path(project) / ".navin" / "reverse" / plan_id


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _write_state(project: Path, plan_id: str, **fields: Any) -> dict[str, Any]:
    path = plan_dir(project, plan_id) / "state.json"
    state = {**_load(path, {}), **fields}
    _save(path, state)
    return state


# ------------------------------------------------------------ board batch ---

_RESOLVED = ("done", "cancelled")


def _refresh_map(store: Any, mapping: dict[str, Any]) -> None:
    by_id = {t["id"]: t for t in store.read_tasks()}
    for entry in mapping.values():
        if entry.get("archived"):
            continue
        task = by_id.get(entry["task_id"])
        if task is None:  # removed by a human: file it again
            entry["status"] = None
            entry["lost"] = True
        else:
            entry["status"] = task.get("status")
            entry["evidence"] = (task.get("evidence") or "")[:500]


def next_batch(project: Path | str, plan_id: str, *, size: int = 30, board_cap: int = 450) -> dict[str, Any]:
    """File the next ready steps on the board; archive finished ones when the board fills."""
    from navin.board.store import ProjectBoardStore

    project = Path(project)
    folder = plan_dir(project, plan_id)
    plan = _load(folder / "rebuild-plan.json", None)
    if not plan:
        raise RuntimeError(f"no rebuild plan in {folder}: run `plan {plan_id}` first")
    map_path = folder / "board-map.json"
    mapping: dict[str, Any] = _load(map_path, {})
    store = ProjectBoardStore(project)
    _refresh_map(store, mapping)
    for sid in [s for s, e in mapping.items() if e.get("lost")]:
        del mapping[sid]
    open_ids = [s for s, e in mapping.items() if not e.get("archived") and e.get("status") not in _RESOLVED]
    capacity = max(0, size - len(open_ids))
    tasks = store.read_tasks()
    if len(tasks) + capacity > board_cap:
        for sid, entry in mapping.items():
            if not entry.get("archived") and entry.get("status") in _RESOLVED:
                store.delete_task(entry["task_id"], actor="navin-reverse", actor_type="agent")
                entry["archived"] = True
        tasks = store.read_tasks()
    capacity = min(capacity, max(0, board_cap - len(tasks)))
    filed: list[dict[str, str]] = []
    for step in plan["steps"]:
        if len(filed) >= capacity:
            break
        if step["id"] in mapping:
            continue
        if any(d not in mapping for d in step["depends_on"]):
            continue
        files = step["files"]
        listing = "\n".join(f"- {f}" for f in files[:40])
        more = f"\n- ... {len(files) - 40} more in rebuild-plan.json" if len(files) > 40 else ""
        origin = step.get("source_root") or plan["source_root"]
        prefix = step.get("prefix") or ""
        where = (f"Original source: {origin} (rebuild path {prefix}/X comes from {origin}/X)"
                 if prefix else f"Original source: {origin}")
        description = (
            f"Reverse rebuild {plan_id}, step {step['id']} (wave {step['wave']}, component {step['component']}).\n"
            f"{where}\n"
            f"Files ({len(files)}):\n{listing}{more}"
        )[:3900]
        deps = [mapping[d]["task_id"] for d in step["depends_on"] if not mapping[d].get("archived")]
        task = store.create_task(
            title=step["title"], actor="navin-reverse", actor_type="agent", description=description,
            status="planned", priority="high" if step["wave"] == 0 else "medium",
            labels=["reverse", plan_id[:40]], depends_on=deps, acceptance=step["acceptance"][:400],
            validation="verify",
        )
        mapping[step["id"]] = {"task_id": task["id"], "status": task["status"]}
        filed.append({"step": step["id"], "task_id": task["id"], "title": step["title"]})
    _save(map_path, mapping)
    done = sum(1 for e in mapping.values() if e.get("status") in _RESOLVED)
    total = len(plan["steps"])
    open_now = len(open_ids) + len(filed)
    state = _write_state(project, plan_id, total=total, done=done, open=open_now,
                         remaining=total - len(mapping), source=plan["source_root"])
    if filed:
        message = f"filed {len(filed)} step(s); work them, then run next-batch again"
    elif open_now:
        message = f"{open_now} reverse task(s) still open: finish them before the next batch"
    elif done >= total:
        message = f"every step is done: run `python -m navin.reverse.repo parity {plan_id}`"
    else:
        message = "nothing ready: a dependency is blocked"
    return {"filed": filed, "state": state, "message": message}


# ----------------------------------------------------------------- parity ---

def parity(project: Path | str, plan_id: str, *, rebuild: Path | str | None = None,
           threshold: float = 1.0, component: str | None = None) -> dict[str, Any]:
    """Per component coverage of the original files by the rebuild tree."""
    project = Path(project)
    folder = plan_dir(project, plan_id)
    inv = _load(folder / "inventory.json", None)
    if not inv:
        raise RuntimeError(f"no inventory in {folder}")
    target = Path(rebuild) if rebuild else project
    rows: list[dict[str, Any]] = []
    for comp in inv["components"]:
        if component and comp["id"] != component:
            continue
        wanted = [f for f in comp["files"] if _kind(f) != "doc"]
        missing = [f for f in wanted if not (target / f).exists()]
        coverage = 1.0 if not wanted else (len(wanted) - len(missing)) / len(wanted)
        orig_tests = [f for f in comp["files"] if _TEST_RE.search(f)]
        tests_ok = not orig_tests or any((target / f).exists() for f in orig_tests)
        rows.append({"component": comp["id"], "path": comp["path"], "files": len(wanted),
                     "missing": len(missing), "coverage": round(coverage, 4), "tests_ported": tests_ok,
                     "complete": coverage >= threshold and tests_ok, "missing_sample": missing[:20]})
    complete = sum(1 for r in rows if r["complete"])
    total_files = sum(r["files"] for r in rows)
    missing_files = sum(r["missing"] for r in rows)
    result = {
        "rebuild": str(target), "threshold": threshold,
        "components": len(rows), "complete": complete,
        "percent": round(100 * complete / len(rows), 2) if rows else 100.0,
        "file_coverage": round(100 * (total_files - missing_files) / total_files, 2) if total_files else 100.0,
        "finished": complete == len(rows),
        "gaps": [r for r in rows if not r["complete"]],
    }
    if component is None:
        _save(folder / "parity.json", result)
        _write_state(project, plan_id, parity_percent=result["percent"], finished=result["finished"])
    return result


# ---------------------------------------------------------------- runtime ---

def runtime_lines(project: Path | str, *, touched: set[str] | None = None) -> list[str]:
    """One line per unfinished rebuild so Build keeps chaining batches.

    With *touched* (the board task ids a chat worked on), only the rebuilds
    that chat drives are shown: another chat must not be steered into them.
    """
    root = Path(project) / ".navin" / "reverse"
    if not root.is_dir():
        return []
    lines: list[str] = []
    for state_path in sorted(root.glob("*/state.json")):
        state = _load(state_path, {})
        if not state.get("total") or state.get("finished"):
            continue
        if touched is not None:
            mapping = _load(state_path.parent / "board-map.json", {})
            if not touched & {e.get("task_id") for e in mapping.values() if isinstance(e, dict)}:
                continue
        pid = state_path.parent.name
        lines.append(
            f"Reverse rebuild {pid} ({state.get('source')}) is in progress: {state.get('done', 0)}/"
            f"{state['total']} steps done, {state.get('open', 0)} on the board. Keep going without "
            f"stopping: work the ready reverse tasks; when none is ready run `python -m navin.reverse.repo "
            f"next-batch {pid}`; when every step is done run `python -m navin.reverse.repo parity {pid}` "
            "and file each gap. The rebuild ends only when parity reports finished."
        )
    return lines


# -------------------------------------------------------------------- cli ---

def _arg(args: list[str], flag: str, default: str | None = None) -> str | None:
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            value = args[i + 1]
            del args[i:i + 2]
            return value
    return default


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    project = Path(_arg(args, "--project", os.getcwd()) or os.getcwd())
    if len(args) < 2 or args[0] not in {"inventory", "plan", "next-batch", "parity", "status"}:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = args[0]
    try:
        if cmd == "inventory":
            ref = _arg(args, "--ref")
            plan_id = _arg(args, "--id")
            refresh = "--refresh" in args
            targets = [a for a in args[1:] if a != "--refresh"]
            sources: list[dict[str, Any]] = []
            for target in targets:
                spec = parse_target(target)
                root, commit = fetch_source(spec, ref=spec.get("ref") or ref, refresh=refresh)
                sources.append({"target": target, "spec": spec, "source_root": str(root), "commit": commit,
                                "ref": spec.get("ref") or ref,
                                "inventory": build_repo_inventory(root, name=spec["name"])})
            names = [s["spec"]["name"] for s in sources]
            for s in sources:
                dup = names.count(s["spec"]["name"]) > 1 and s["spec"].get("owner")
                s["prefix"] = f"{s['spec']['owner']}-{s['spec']['name']}" if dup else s["spec"]["name"]
            first = sources[0]["spec"]
            plan_id = plan_id or _slug("-".join([first.get("owner") or "local", *names]))
            if len(sources) == 1:
                inv = sources[0]["inventory"]
                inv.update(target=targets[0], source_root=sources[0]["source_root"],
                           commit=sources[0]["commit"], ref=sources[0]["ref"])
            else:
                inv = merge_inventories(sources)
                inv.update(target=" ".join(targets), source_root=", ".join(s["source_root"] for s in sources),
                           commit=None, ref=None)
            inv["sources"] = [{k: s[k] for k in ("target", "source_root", "commit", "ref")}
                              | {"prefix": s["prefix"] if len(sources) > 1 else ""} for s in sources]
            _save(plan_dir(project, plan_id) / "inventory.json", inv)
            _write_state(project, plan_id, source=" ".join(targets),
                         commit=inv["commit"] or [s["commit"] for s in sources])
            summary = {k: inv[k] for k in ("target", "source_root", "commit", "license", "languages", "counts",
                                            "compose_services", "sources")}
            summary["id"] = plan_id
            summary["components"] = [{"id": c["id"], "path": c["path"], "ecosystem": c["ecosystem"],
                                      "depends": c["internal_depends"], **c["counts"]} for c in inv["components"]]
            print(json.dumps(summary, indent=1))
        elif cmd == "plan":
            max_files = int(_arg(args, "--max-files", "60") or 60)
            folder = plan_dir(project, args[1])
            inv = _load(folder / "inventory.json", None)
            if not inv:
                raise RuntimeError(f"no inventory in {folder}: run `inventory` first")
            plan = build_plan(inv, source_root=inv["source_root"], max_files=max_files)
            _save(folder / "rebuild-plan.json", plan)
            _write_state(project, args[1], total=plan["counts"]["steps"], done=0, open=0, finished=False)
            print(json.dumps({"counts": plan["counts"], "cycles": plan["cycles"],
                              "first_wave": plan["waves"][:2]}, indent=1))
        elif cmd == "next-batch":
            size = int(_arg(args, "--size", "30") or 30)
            print(json.dumps(next_batch(project, args[1], size=size), indent=1))
        elif cmd == "parity":
            rebuild = _arg(args, "--rebuild")
            threshold = float(_arg(args, "--threshold", "1.0") or 1.0)
            comp = _arg(args, "--component")
            result = parity(project, args[1], rebuild=rebuild, threshold=threshold, component=comp)
            print(json.dumps(result, indent=1))
            return 0 if result["finished"] else 1
        else:
            print(json.dumps(_load(plan_dir(project, args[1]) / "state.json", {}), indent=1))
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
