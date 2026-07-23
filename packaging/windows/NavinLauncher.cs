// navin.exe — all-in-one Windows launcher for Navin.
//
// What it does, in order:
//   1. Finds Python 3.11+ (py launcher, python, common install paths).
//      If missing, offers to install it silently through winget.
//   2. Creates/reuses a dedicated venv at %USERPROFILE%\.navin\venv.
//   3. Installs (first run) or reuses Navin from GitHub EIAGEN/navin-claw.
//   4. Starts the gateway and opens the WebUI in the browser.
//      All configuration (providers, models, channels) happens in the platform.
//
// Usage:
//   navin.exe              install if needed + start WebUI
//   navin.exe --update     force reinstall/upgrade of Navin, then start
//   navin.exe <args...>    pass through to the navin CLI (e.g. `navin.exe status`)
//
// Environment overrides:
//   NAVIN_EXE_SOURCE  pip install target (default: GitHub main zip)
//   NAVIN_VENV        venv directory (default: %USERPROFILE%\.navin\venv)
//
// Build (any Windows machine, no SDK needed — csc ships with .NET Framework):
//   powershell -ExecutionPolicy Bypass -File packaging\windows\build-launcher.ps1

using System;
using System.Diagnostics;
using System.IO;

static class NavinLauncher
{
    const string DefaultSource =
        "https://github.com/EIAGEN/navin-claw/archive/refs/heads/main.zip";

    static string HomeDir
    {
        get { return Environment.GetFolderPath(Environment.SpecialFolder.UserProfile); }
    }

    static string VenvDir
    {
        get
        {
            string custom = Environment.GetEnvironmentVariable("NAVIN_VENV");
            return string.IsNullOrEmpty(custom)
                ? Path.Combine(HomeDir, ".navin", "venv")
                : custom;
        }
    }

    static string VenvPython
    {
        get { return Path.Combine(VenvDir, "Scripts", "python.exe"); }
    }

    static string InstallSource
    {
        get
        {
            string custom = Environment.GetEnvironmentVariable("NAVIN_EXE_SOURCE");
            return string.IsNullOrEmpty(custom) ? DefaultSource : custom;
        }
    }

    static int Main(string[] args)
    {
        Console.Title = "Navin";
        Info("Navin launcher — https://github.com/EIAGEN/navin-claw");
        Info("");

        bool update = args.Length > 0 && args[0] == "--update";
        string[] navinArgs = update ? new string[0] : args;

        try
        {
            bool installed = File.Exists(VenvPython) && NavinInstalled();
            if (!installed || update)
            {
                string python = FindPython();
                if (python == null)
                {
                    python = OfferWingetInstall();
                    if (python == null)
                    {
                        Error("Python 3.11+ is required. Install it from https://www.python.org/downloads/ then run navin.exe again.");
                        return Pause(1);
                    }
                }
                Info("Using Python: " + python);
                EnsureVenv(python);
                InstallNavin();
            }

            if (navinArgs.Length > 0)
            {
                // Pass-through mode: navin.exe status, navin.exe agent -m "..." etc.
                return RunNavin(string.Join(" ", QuoteAll(navinArgs)), true);
            }

            Info("");
            Info("Starting the Navin gateway + WebUI (Ctrl+C to stop)...");
            Info("Configure your model provider directly in the platform (Settings -> Providers).");
            return RunNavin("webui --yes", true);
        }
        catch (Exception ex)
        {
            Error("Unexpected error: " + ex.Message);
            return Pause(1);
        }
    }

    // ------------------------------------------------------------------
    // Python discovery / installation
    // ------------------------------------------------------------------

    static string FindPython()
    {
        // Try the py launcher first (most reliable on Windows), then python,
        // then well-known install directories.
        string[] candidates = new string[]
        {
            "py -3.13", "py -3.12", "py -3.11", "py -3", "python",
        };
        foreach (string candidate in candidates)
        {
            if (IsPython311(candidate)) return candidate;
        }

        string localPrograms = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", "Python");
        if (Directory.Exists(localPrograms))
        {
            foreach (string dir in Directory.GetDirectories(localPrograms))
            {
                string exe = Path.Combine(dir, "python.exe");
                if (File.Exists(exe) && IsPython311(Quote(exe))) return Quote(exe);
            }
        }
        return null;
    }

    static bool IsPython311(string command)
    {
        string output = Capture(command +
            " -c \"import sys; print('OK' if sys.version_info >= (3, 11) else 'OLD')\"");
        return output != null && output.Contains("OK");
    }

    static string OfferWingetInstall()
    {
        if (Capture("winget --version") == null)
        {
            return null;
        }
        Info("Python 3.11+ was not found on this machine.");
        Console.Write("Install Python 3.12 automatically with winget? [Y/n] ");
        string answer = (Console.ReadLine() ?? "").Trim().ToLowerInvariant();
        if (answer == "n" || answer == "no" || answer == "non")
        {
            return null;
        }
        Info("Installing Python 3.12 (this can take a few minutes)...");
        int code = Run("winget install -e --id Python.Python.3.12 " +
            "--silent --accept-package-agreements --accept-source-agreements", true);
        if (code != 0)
        {
            Error("winget could not install Python.");
            return null;
        }
        // Fresh installs are not on PATH of this process; probe known locations.
        string found = FindPython();
        if (found == null)
        {
            Error("Python was installed but not found yet. Close this window and run navin.exe again.");
        }
        return found;
    }

    // ------------------------------------------------------------------
    // Venv + Navin install
    // ------------------------------------------------------------------

    static void EnsureVenv(string python)
    {
        if (File.Exists(VenvPython)) return;
        Info("Creating a dedicated environment at " + VenvDir + "...");
        Directory.CreateDirectory(Path.GetDirectoryName(VenvDir));
        if (Run(python + " -m venv " + Quote(VenvDir), true) != 0)
        {
            throw new Exception("could not create the virtual environment.");
        }
    }

    static void InstallNavin()
    {
        Info("Installing Navin from " + InstallSource + "...");
        Run(Quote(VenvPython) + " -m pip install --upgrade pip", false);
        if (Run(Quote(VenvPython) + " -m pip install --upgrade " + Quote(InstallSource), true) != 0)
        {
            throw new Exception("pip could not install Navin. Check your internet connection and retry.");
        }
        Info("Navin installed.");
    }

    static bool NavinInstalled()
    {
        return Capture(Quote(VenvPython) + " -m navin --version") != null;
    }

    static int RunNavin(string navinArgs, bool interactive)
    {
        return Run(Quote(VenvPython) + " -m navin " + navinArgs, interactive);
    }

    // ------------------------------------------------------------------
    // Process helpers
    // ------------------------------------------------------------------

    static int Run(string commandLine, bool interactive)
    {
        ProcessStartInfo psi = new ProcessStartInfo("cmd.exe", "/d /s /c \"" + commandLine + "\"");
        psi.UseShellExecute = false;
        using (Process process = Process.Start(psi))
        {
            process.WaitForExit();
            return process.ExitCode;
        }
    }

    static string Capture(string commandLine)
    {
        try
        {
            ProcessStartInfo psi = new ProcessStartInfo("cmd.exe", "/d /s /c \"" + commandLine + "\"");
            psi.UseShellExecute = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.CreateNoWindow = true;
            using (Process process = Process.Start(psi))
            {
                string output = process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                process.WaitForExit();
                return process.ExitCode == 0 ? output : null;
            }
        }
        catch
        {
            return null;
        }
    }

    static string Quote(string value)
    {
        return "\"" + value + "\"";
    }

    static string[] QuoteAll(string[] values)
    {
        string[] quoted = new string[values.Length];
        for (int i = 0; i < values.Length; i++)
        {
            quoted[i] = values[i].IndexOf(' ') >= 0 ? Quote(values[i]) : values[i];
        }
        return quoted;
    }

    static int Pause(int code)
    {
        // Keep the window open when double-clicked from Explorer.
        Console.WriteLine();
        Console.Write("Press Enter to close...");
        try { Console.ReadLine(); } catch { }
        return code;
    }

    static void Info(string message) { Console.WriteLine(message); }

    static void Error(string message)
    {
        ConsoleColor previous = Console.ForegroundColor;
        Console.ForegroundColor = ConsoleColor.Red;
        Console.WriteLine(message);
        Console.ForegroundColor = previous;
    }
}
