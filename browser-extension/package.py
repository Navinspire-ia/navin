"""Package local browser builds alongside Navin's Python distribution."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent
target = root.parent / "navin" / "browser_extension"
target.mkdir(parents=True, exist_ok=True)
for browser in ("chrome", "edge", "firefox"):
    source = root / "dist" / browser
    with ZipFile(target / f"{browser}.zip", "w", ZIP_DEFLATED) as archive:
        for item in sorted(source.iterdir()):
            if item.is_file():
                archive.write(item, item.name)
    print(f"Packaged {browser}: {target / (browser + '.zip')}")
