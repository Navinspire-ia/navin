#!/usr/bin/env python3
"""Download curated open-licensed fonts for canvas work.

Usage:
    fetch_fonts.py --list                      print the manifest (no network)
    fetch_fonts.py "Crimson Pro" "IBM Plex Mono"  download these families
    fetch_fonts.py --all                       download every family
    fetch_fonts.py --dest ./fonts Inter        choose the destination folder

Files land in ~/.cache/navin/fonts by default. Existing files are skipped, so
the command can be re-run safely. Only the download itself touches the network.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAW_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
DEFAULT_DEST = Path.home() / ".cache" / "navin" / "fonts"

# family -> (folder in google/fonts ofl/, [file names], role, pairs with)
# Every family here is published under the SIL Open Font License 1.1.
MANIFEST: dict[str, dict] = {
    "Inter": {
        "folder": "inter",
        "files": ["Inter[opsz,wght].ttf"],
        "role": "UI and body sans (Navin house face)",
        "pair": "IBM Plex Mono",
    },
    "IBM Plex Sans": {
        "folder": "ibmplexsans",
        "files": ["IBMPlexSans[wdth,wght].ttf"],
        "role": "Body sans, fallback for Inter",
        "pair": "IBM Plex Mono",
    },
    "IBM Plex Mono": {
        "folder": "ibmplexmono",
        "files": ["IBMPlexMono-Regular.ttf", "IBMPlexMono-Bold.ttf"],
        "role": "Mono for marks, indices and code",
        "pair": "Inter",
    },
    "IBM Plex Serif": {
        "folder": "ibmplexserif",
        "files": ["IBMPlexSerif-Regular.ttf"],
        "role": "Editorial serif with a technical edge",
        "pair": "IBM Plex Sans",
    },
    "JetBrains Mono": {
        "folder": "jetbrainsmono",
        "files": ["JetBrainsMono[wght].ttf"],
        "role": "Mono, brutalist labels",
        "pair": "Bricolage Grotesque",
    },
    "Lora": {
        "folder": "lora",
        "files": ["Lora[wght].ttf", "Lora-Italic[wght].ttf"],
        "role": "Warm editorial serif, long titles",
        "pair": "Inter",
    },
    "Crimson Pro": {
        "folder": "crimsonpro",
        "files": ["CrimsonPro[wght].ttf", "CrimsonPro-Italic[wght].ttf"],
        "role": "Book serif, italic display",
        "pair": "Inter",
    },
    "Source Serif 4": {
        "folder": "sourceserif4",
        "files": ["SourceSerif4[opsz,wght].ttf"],
        "role": "Readable serif with optical sizes",
        "pair": "Inter",
    },
    "Libre Baskerville": {
        "folder": "librebaskerville",
        "files": ["LibreBaskerville[wght].ttf"],
        "role": "Classic transitional serif, museum captions",
        "pair": "Work Sans",
    },
    "Instrument Serif": {
        "folder": "instrumentserif",
        "files": ["InstrumentSerif-Regular.ttf", "InstrumentSerif-Italic.ttf"],
        "role": "Condensed display serif, quiet scientific posters",
        "pair": "IBM Plex Mono",
    },
    "Instrument Sans": {
        "folder": "instrumentsans",
        "files": ["InstrumentSans[wdth,wght].ttf"],
        "role": "Neutral sans with width axis",
        "pair": "Instrument Serif",
    },
    "Fraunces": {
        "folder": "fraunces",
        "files": ["Fraunces[SOFT,WONK,opsz,wght].ttf"],
        "role": "Soft display serif, expressive covers",
        "pair": "Manrope",
    },
    "DM Serif Display": {
        "folder": "dmserifdisplay",
        "files": ["DMSerifDisplay-Regular.ttf"],
        "role": "High-contrast display serif, one-word titles",
        "pair": "DM Mono",
    },
    "Young Serif": {
        "folder": "youngserif",
        "files": ["YoungSerif-Regular.ttf"],
        "role": "Sturdy display serif",
        "pair": "Work Sans",
    },
    "Gloock": {
        "folder": "gloock",
        "files": ["Gloock-Regular.ttf"],
        "role": "Heavy didone display",
        "pair": "Inter",
    },
    "Work Sans": {
        "folder": "worksans",
        "files": ["WorkSans[wght].ttf"],
        "role": "Light grotesque for labels",
        "pair": "Young Serif",
    },
    "Manrope": {
        "folder": "manrope",
        "files": ["Manrope[wght].ttf"],
        "role": "Geometric sans, modern covers",
        "pair": "Fraunces",
    },
    "Outfit": {
        "folder": "outfit",
        "files": ["Outfit[wght].ttf"],
        "role": "Rounded geometric display sans",
        "pair": "DM Mono",
    },
    "Space Grotesk": {
        "folder": "spacegrotesk",
        "files": ["SpaceGrotesk[wght].ttf"],
        "role": "Quirky grotesque, tech posters",
        "pair": "Space Mono",
    },
    "Space Mono": {
        "folder": "spacemono",
        "files": ["SpaceMono-Regular.ttf"],
        "role": "Mono with character",
        "pair": "Space Grotesk",
    },
    "DM Mono": {
        "folder": "dmmono",
        "files": ["DMMono-Regular.ttf"],
        "role": "Light mono for small marks",
        "pair": "DM Serif Display",
    },
    "Red Hat Mono": {
        "folder": "redhatmono",
        "files": ["RedHatMono[wght].ttf"],
        "role": "Clean mono, technical diagrams",
        "pair": "Tektur",
    },
    "Bricolage Grotesque": {
        "folder": "bricolagegrotesque",
        "files": ["BricolageGrotesque[opsz,wdth,wght].ttf"],
        "role": "Brutalist display grotesque",
        "pair": "JetBrains Mono",
    },
    "Big Shoulders Display": {
        "folder": "bigshouldersdisplay",
        "files": ["BigShouldersDisplay[wght].ttf"],
        "role": "Tall condensed display, posters",
        "pair": "JetBrains Mono",
    },
    "Archivo Black": {
        "folder": "archivoblack",
        "files": ["ArchivoBlack-Regular.ttf"],
        "role": "Heavy grotesque, single-word statements",
        "pair": "Work Sans",
    },
    "Boldonse": {
        "folder": "boldonse",
        "files": ["Boldonse-Regular.ttf"],
        "role": "Ultra-wide expressive display (one word only)",
        "pair": "Work Sans",
    },
    "Erica One": {
        "folder": "ericaone",
        "files": ["EricaOne-Regular.ttf"],
        "role": "Brush display, punk posters",
        "pair": "Work Sans",
    },
    "Tektur": {
        "folder": "tektur",
        "files": ["Tektur[wdth,wght].ttf"],
        "role": "Technical display with width axis",
        "pair": "Red Hat Mono",
    },
    "Jura": {
        "folder": "jura",
        "files": ["Jura[wght].ttf"],
        "role": "Thin futuristic sans",
        "pair": "Red Hat Mono",
    },
    "Silkscreen": {
        "folder": "silkscreen",
        "files": ["Silkscreen-Regular.ttf"],
        "role": "Pixel display, retro marks",
        "pair": "Space Mono",
    },
}


def font_url(folder: str, filename: str) -> str:
    """Raw GitHub URL for one font file (brackets are percent-encoded)."""
    return RAW_BASE + folder + "/" + urllib.parse.quote(filename)


def print_manifest(as_json: bool = False) -> None:
    if as_json:
        data = {
            name: {**spec, "urls": [font_url(spec["folder"], f) for f in spec["files"]]}
            for name, spec in MANIFEST.items()
        }
        print(json.dumps(data, indent=2))
        return
    width = max(len(n) for n in MANIFEST)
    print(f"{'Family'.ljust(width)}  Role  (pairs with)")
    for name, spec in MANIFEST.items():
        print(f"{name.ljust(width)}  {spec['role']}  ({spec['pair']})")
        for f in spec["files"]:
            print(f"{''.ljust(width)}    {font_url(spec['folder'], f)}")
    print(f"\n{len(MANIFEST)} families, all SIL Open Font License 1.1. Default folder: {DEFAULT_DEST}")


def resolve(names: list[str]) -> list[str]:
    """Match requested names case-insensitively, ignoring spaces."""
    norm = {k.lower().replace(" ", ""): k for k in MANIFEST}
    out: list[str] = []
    for n in names:
        key = n.lower().replace(" ", "")
        if key not in norm:
            raise SystemExit(f"unknown family: {n!r}. Run --list to see the manifest.")
        out.append(norm[key])
    return out


def download(family: str, dest: Path, timeout: float = 30.0) -> list[Path]:
    spec = MANIFEST[family]
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename in spec["files"]:
        target = dest / filename
        if target.exists() and target.stat().st_size > 0:
            print(f"skip  {target} (already present)")
            written.append(target)
            continue
        url = font_url(spec["folder"], filename)
        print(f"fetch {url}")
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
        except urllib.error.URLError as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(target)
        print(f"  wrote {target} ({len(data) // 1024} KB)")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("families", nargs="*", help="font families to download (see --list)")
    parser.add_argument("--list", action="store_true", help="print the curated manifest and exit")
    parser.add_argument("--json", action="store_true", help="with --list, print JSON")
    parser.add_argument("--all", action="store_true", help="download every family in the manifest")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"destination folder (default {DEFAULT_DEST})")
    args = parser.parse_args(argv)

    if args.list:
        print_manifest(as_json=args.json)
        return 0
    names = list(MANIFEST) if args.all else resolve(args.families)
    if not names:
        parser.print_help()
        return 2
    total = 0
    for family in names:
        total += len(download(family, args.dest))
    print(f"\n{total} file(s) available in {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
