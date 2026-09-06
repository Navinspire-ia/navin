import { describe, expect, it } from "vitest";

import {
  FORGE_ENV_VARS,
  forgeCopy,
  forgeEnvVar,
  forgeSetupHint,
  isMergeRequestForge,
} from "@/lib/forge";
import type { GithubPrViewPayload } from "@/lib/types";

function view(
  overrides: Partial<GithubPrViewPayload> = {},
): GithubPrViewPayload {
  return {
    available: true,
    branch: "feature/x",
    detail: "",
    pr: null,
    forge: "forgejo",
    host: "forgejo.navinspire.ai",
    token_configured: true,
    ...overrides,
  };
}

describe("forge wording", () => {
  it("only GitLab speaks of merge requests", () => {
    expect(isMergeRequestForge("gitlab")).toBe(true);
    expect(isMergeRequestForge("github")).toBe(false);
    expect(isMergeRequestForge("forgejo")).toBe(false);
    expect(isMergeRequestForge(undefined)).toBe(false);
  });

  it("gives GitLab its own labels", () => {
    const copy = forgeCopy("gitlab");
    expect(copy.create.fallback).toBe("Create Merge Request");
    expect(copy.draft.key).toBe("dev.git.draftMr");
    expect(copy.failed.fallback).toContain("merge request");
  });

  it("keeps pull request wording everywhere else", () => {
    for (const kind of ["github", "forgejo", "unknown"] as const) {
      const copy = forgeCopy(kind);
      expect(copy.create.fallback).toBe("Create PR");
      expect(copy.commitAndCreate.key).toBe("dev.git.commitAndCreatePr");
    }
  });

  it("names the env var the backend reads", () => {
    expect(forgeEnvVar("github")).toBe("GITHUB_TOKEN");
    expect(forgeEnvVar("gitlab")).toBe("GITLAB_TOKEN");
    expect(forgeEnvVar("forgejo")).toBe("FORGEJO_TOKEN");
    expect(forgeEnvVar("unknown")).toBeNull();
    expect(Object.keys(FORGE_ENV_VARS)).toHaveLength(3);
  });
});

describe("forge setup hint", () => {
  it("stays silent when a token is configured", () => {
    expect(forgeSetupHint(view())).toBeNull();
  });

  it("stays silent for a repository with no forge remote", () => {
    expect(forgeSetupHint(view({ host: "", forge: "unknown" }))).toBeNull();
    expect(forgeSetupHint(null)).toBeNull();
    expect(forgeSetupHint(undefined)).toBeNull();
  });

  it("asks for a token and names the host", () => {
    const hint = forgeSetupHint(view({ token_configured: false }));
    expect(hint?.key).toBe("dev.git.forgeTokenMissing");
    expect(hint?.host).toBe("forgejo.navinspire.ai");
    expect(hint?.env).toBe("FORGEJO_TOKEN");
  });

  it("asks for the forge type when the host is unrecognized", () => {
    const hint = forgeSetupHint(
      view({ forge: "unknown", host: "code.example.com", token_configured: true }),
    );
    expect(hint?.key).toBe("dev.git.forgeUnknownHint");
    expect(hint?.env).toBeNull();
  });

  it("never mentions the gh CLI", () => {
    const hint = forgeSetupHint(view({ token_configured: false }));
    expect(hint?.fallback.toLowerCase()).not.toContain("gh cli");
  });
});
