#!/bin/sh
# Ship the OpenSSL cryptography linked, not an older libssl.3.dylib.
set -eu
dist="${1:?navin-dist}"
python3 - "$dist" <<'PY'
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd):
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def otool_deps(path):
    result = run(["otool", "-L", str(path)])
    deps = []
    for line in result.stdout.splitlines()[1:]:
        name = line.strip().split(" (compatibility", 1)[0]
        if name:
            deps.append(name)
    return deps


def has_group_symbol(path):
    return "_SSL_get0_group_name" in run(["nm", "-gU", str(path)]).stdout


def interesting(path):
    if not path.is_file():
        return False
    if path.suffix in {".so", ".dylib"}:
        return True
    return path.suffix == "" and os.access(path, os.X_OK)


def mach_archs(path):
    result = run(["lipo", "-archs", str(path)])
    if result.returncode != 0:
        return set()
    return {part for part in result.stdout.split() if part}


dist = Path(sys.argv[1])
bundled = list(dist.rglob("libssl.3.dylib"))
if not bundled:
    print("no bundled libssl.3.dylib")
    raise SystemExit(0)
ssl_dest = next((path for path in bundled if path.parent.name == "_internal"), bundled[0])
if has_group_symbol(ssl_dest):
    print(f"openssl ok: {ssl_dest}")
    raise SystemExit(0)
crypto_dest = ssl_dest.with_name("libcrypto.3.dylib")
libdirs = []
extra = os.environ.get("NAVIN_OPENSSL_LIB", "").strip()
if extra:
    libdirs.append(Path(extra))
libdirs.extend([Path("/usr/local/opt/openssl@3/lib"), Path("/opt/homebrew/opt/openssl@3/lib")])
prefix = run(["brew", "--prefix", "openssl@3"])
if prefix.returncode == 0 and prefix.stdout.strip():
    libdirs.append(Path(prefix.stdout.strip()) / "lib")
donors = []
for libdir in libdirs:
    candidate = libdir / "libssl.3.dylib"
    if candidate.is_file():
        donors.append(candidate)
for rust in dist.rglob("_rust.abi3.so"):
    for dep in otool_deps(rust):
        if dep.startswith("/") and dep.endswith("libssl.3.dylib") and Path(dep).is_file():
            donors.append(Path(dep))
seen = set()
unique = []
bundled_archs = mach_archs(ssl_dest)
for donor in donors:
    resolved = donor.resolve()
    if resolved in seen or not has_group_symbol(donor):
        continue
    # An Apple Silicon runner also has an arm64 Homebrew OpenSSL. Copying
    # that into an x86_64 sidecar would ship the wrong slice.
    if bundled_archs and mach_archs(donor).isdisjoint(bundled_archs):
        print(f"skip donor {donor}: arch does not match {sorted(bundled_archs)}")
        continue
    seen.add(resolved)
    unique.append(donor)
if not unique:
    print("no OpenSSL with SSL_get0_group_name", file=sys.stderr)
    raise SystemExit(1)
donor_ssl = unique[0]
donor_crypto = donor_ssl.with_name("libcrypto.3.dylib")
if not donor_crypto.is_file():
    print(f"missing {donor_crypto}", file=sys.stderr)
    raise SystemExit(1)
print(f"replacing {ssl_dest} with {donor_ssl}")
for dest, src in ((ssl_dest, donor_ssl), (crypto_dest, donor_crypto)):
    if dest.exists():
        dest.chmod(0o755)
    shutil.copy2(src, dest)
    dest.chmod(0o755)
for dest in (ssl_dest, crypto_dest):
    subprocess.check_call(["install_name_tool", "-id", f"@loader_path/{dest.name}", str(dest)])
    for dep in otool_deps(dest):
        base = Path(dep).name
        if base in {"libssl.3.dylib", "libcrypto.3.dylib"} and not dep.startswith("@loader_path/"):
            subprocess.check_call(["install_name_tool", "-change", dep, f"@loader_path/{base}", str(dest)])
changed = {ssl_dest, crypto_dest}
for path in dist.rglob("*"):
    if not interesting(path) or path in {ssl_dest, crypto_dest}:
        continue
    for dep in otool_deps(path):
        base = Path(dep).name
        if base not in {"libssl.3.dylib", "libcrypto.3.dylib"}:
            continue
        target = ssl_dest if base.startswith("libssl") else crypto_dest
        rel = Path(os.path.relpath(target, path.parent)).as_posix()
        new = "@loader_path/" + rel
        if dep == new:
            continue
        path.chmod(path.stat().st_mode | 0o200)
        subprocess.check_call(["install_name_tool", "-change", dep, new, str(path)])
        changed.add(path)
for path in changed:
    subprocess.check_call(["codesign", "--force", "--sign", "-", str(path)])
navin = dist / "navin"
if navin.is_file():
    subprocess.check_call(["codesign", "--force", "--sign", "-", str(navin)])
if not has_group_symbol(ssl_dest):
    print("swap failed", file=sys.stderr)
    raise SystemExit(1)
print("openssl swap ok")
PY
