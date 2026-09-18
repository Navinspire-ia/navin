{% if system == 'Windows' and workspace_is_wsl %}
## Platform Policy (Windows host, WSL project)
- This project lives inside a WSL distribution. Project commands and Git run automatically inside that distribution, using Linux syntax and its installed toolchain.
- Do not wrap project commands in `wsl` or `wsl.exe`, and do not use PowerShell syntax for them. Omit the shell override for bash, or name the required Linux shell.
- Prefer workspace-relative file paths. Absolute Linux paths returned by commands refer to that same distribution; file tools translate them for Windows access.
{% elif system == 'Windows' %}
## Platform Policy (Windows)
- You are running on Windows. Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
{% else %}
## Platform Policy (POSIX)
- You are running on a POSIX system. Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
{% endif %}
