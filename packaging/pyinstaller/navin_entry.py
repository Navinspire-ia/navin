"""PyInstaller entry point for the standalone navin binary."""

import multiprocessing
import sys

from navin.cli.commands import app

if __name__ == "__main__":
    # Required for frozen executables that spawn worker processes.
    multiprocessing.freeze_support()
    # Double-clicked with no arguments → behave like the launcher: start the
    # WebUI (which prepares the WebSocket channel, starts the gateway, and
    # opens the browser). With arguments, behave like the normal navin CLI.
    if len(sys.argv) == 1:
        # --yes applies safe local defaults without prompting: everything else
        # (providers, models, channels) is configured directly in the platform.
        sys.argv.extend(["webui", "--yes"])
    app()
