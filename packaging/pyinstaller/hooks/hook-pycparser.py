"""Local PyInstaller override for pycparser.

The upstream contrib hook still assumes ``pycparser.lextab`` and
``pycparser.yacctab`` are generated modules that must be bundled. Recent
pycparser releases keep those names only for backward-compatible constructor
parameters and do not ship the modules. An empty hook removes the false-positive
"Hidden import not found" warnings on Windows builds.
"""

hiddenimports = []
