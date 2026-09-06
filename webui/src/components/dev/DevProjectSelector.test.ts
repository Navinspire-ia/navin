import { HardDrive, Monitor, SquareTerminal, UserRound } from "lucide-react";
import { describe, expect, it } from "vitest";

import i18n from "@/i18n";
import type { FsRootsPayload } from "@/lib/types";

import { rootIcon, rootLabel, rootsPathPlaceholder } from "./DevProjectSelector";

const en = i18n.getFixedT("en");
const fr = i18n.getFixedT("fr");

describe("rootLabel", () => {
  it("words the roots the gateway reports in the user's language", () => {
    expect(rootLabel({ kind: "home", label: "Home", path: "/home/me" }, fr)).toBe(
      "Dossier personnel",
    );
    expect(
      rootLabel({ kind: "windows", label: "Windows (C:)", path: "/mnt/c", name: "C" }, fr),
    ).toBe("Windows (C:)");
    expect(
      rootLabel({ kind: "disk", label: "Disk (D:)", path: "D:\\", name: "D" }, fr),
    ).toBe("Disque (D:)");
    expect(
      rootLabel(
        { kind: "windows-home", label: "Windows home", path: "/mnt/c/Users/me", name: "me" },
        en,
      ),
    ).toBe("Windows home");
  });

  it("keeps names that are already the label: volumes, drives, distributions, /", () => {
    expect(
      rootLabel({ kind: "volume", label: "Backup", path: "/Volumes/Backup", name: "Backup" }, fr),
    ).toBe("Backup");
    expect(
      rootLabel({ kind: "drive", label: "data", path: "/mnt/data", name: "data" }, en),
    ).toBe("data");
    expect(
      rootLabel(
        { kind: "wsl", label: "WSL: Ubuntu", path: "\\\\wsl.localhost\\Ubuntu\\home", name: "Ubuntu" },
        fr,
      ),
    ).toBe("WSL: Ubuntu");
    expect(rootLabel({ kind: "system", label: "/", path: "/" }, fr)).toBe("/");
  });

  it("falls back to the backend wording for a root without a name or of an unknown kind", () => {
    // An older gateway sends no `name`.
    expect(rootLabel({ kind: "windows", label: "Windows (C:)", path: "/mnt/c" }, fr)).toBe(
      "Windows (C:)",
    );
    expect(rootLabel({ kind: "network", label: "NAS", path: "//nas/share" }, en)).toBe("NAS");
  });
});

describe("rootIcon", () => {
  it("tells a Windows drive, the Windows profile, a disk and a distribution apart", () => {
    expect(rootIcon("windows")).toBe(Monitor);
    expect(rootIcon("windows-home")).toBe(UserRound);
    expect(rootIcon("disk")).toBe(HardDrive);
    expect(rootIcon("volume")).toBe(HardDrive);
    expect(rootIcon("drive")).toBe(HardDrive);
    expect(rootIcon("wsl")).toBe(SquareTerminal);
  });
});

describe("rootsPathPlaceholder", () => {
  const payload = (home: string): FsRootsPayload => ({
    environment: "linux",
    roots: [{ kind: "home", label: "Home", path: home }],
  });

  it("shapes the manual path hint like the host's paths", () => {
    expect(rootsPathPlaceholder(payload("/home/me"), "fallback")).toBe("/home/me/project");
    expect(rootsPathPlaceholder(payload("C:\\Users\\me"), "fallback")).toBe(
      "C:\\Users\\me\\project",
    );
  });

  it("uses the fallback until the roots have loaded", () => {
    expect(rootsPathPlaceholder(null, "/absolute/path/to/project")).toBe(
      "/absolute/path/to/project",
    );
    expect(
      rootsPathPlaceholder({ environment: "macos", roots: [] }, "/absolute/path/to/project"),
    ).toBe("/absolute/path/to/project");
  });
});
