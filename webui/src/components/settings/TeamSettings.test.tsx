// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CollabPayload, CollabSettings, TeamMember } from "@/lib/team";

afterEach(cleanup);

function member(id: string, name: string, role: TeamMember["role"]): TeamMember {
  return {
    id, name, role, title: name === "Mark" ? "Senior Developer" : "QA Engineer", specialty: "",
    email: `${id}@team.navin.local`, avatar: "round", color: "#2f7de1", model_preset: "default",
    reasoning_effort: "medium", sandbox: "workspace", prompt: "Be precise.", tools: ["read_file", "team"],
    skills: ["git"], enabled: true,
  };
}

function payload(collab: Partial<CollabSettings> = {}): CollabPayload {
  return {
    collab: {
      enabled: false, pattern: "dev_tester", max_rounds: 5, auto_feedback: true,
      human_approval_critical: true, budget_tokens: 0,
      members: [member("mark", "Mark", "developer"), member("salma", "Salma", "tester")],
      teams: [{ id: "dev", name: "Dev", members: ["mark", "salma"] }],
      default_team: "dev",
      ...collab,
    },
    catalog: {
      roles: [{ key: "developer", tools: ["read_file", "exec"], skills: ["git"], prompt: "Implement.", sandbox: "workspace", effort: "medium" }],
      tools: ["read_file", "exec", "team"],
      skills: [{ name: "git", description: "Git workflow" }, { name: "seo-audit", description: "SEO" }],
      presets: [{ name: "default", label: "Main model", model: "claude" }, { name: "fast", label: "Fast", model: "gpt" }],
      avatars: ["round", "visor"],
      sandboxes: ["workspace", "worktree", "readonly"],
      patterns: ["dev_tester", "debate"],
      efforts: ["", "low", "medium", "high"],
    },
  };
}

const saveCollabSettings = vi.fn(async (_token: string, values: unknown) => {
  const next = payload(values as Partial<CollabSettings>);
  return next;
});
const setCollabEnabled = vi.fn(async (_token: string, enabled: boolean) => payload({ enabled }));

vi.mock("@/lib/api", () => ({
  fetchCollabSettings: vi.fn(async () => payload()),
  saveCollabSettings: (token: string, values: unknown) => saveCollabSettings(token, values),
  setCollabEnabled: (token: string, enabled: boolean) => setCollabEnabled(token, enabled),
}));

import { TeamSettings } from "./TeamSettings";

describe("TeamSettings", () => {
  it("toggles the mode, edits a teammate and a team, and saves the whole team", async () => {
    const onChanged = vi.fn();
    render(<TeamSettings token="tok" onChanged={onChanged} />);
    await screen.findByTestId("team-settings");

    fireEvent.click(screen.getByTestId("settings-collab-toggle"));
    await waitFor(() => expect(setCollabEnabled).toHaveBeenCalledWith("tok", true));
    expect(onChanged).toHaveBeenCalled();

    // Rename Mark, pick another model, add a skill.
    // Three panes: General, Teams, Members.
    fireEvent.click(screen.getByRole("tab", { name: /Members/ }));
    fireEvent.change(screen.getByDisplayValue("Mark"), { target: { value: "Marc" } });
    // The model list opens below the field, scrolls and can be searched.
    fireEvent.click(screen.getByRole("button", { name: "Model" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search a model" }), { target: { value: "gpt" } });
    fireEvent.click(screen.getByRole("option", { name: /Fast/ }));
    fireEvent.click(screen.getByRole("button", { name: /seo-audit/ }));

    // New team with only Salma, made default.
    fireEvent.click(screen.getByRole("tab", { name: /Teams/ }));
    fireEvent.click(screen.getByRole("button", { name: "New team" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /Salma/ }));
    fireEvent.click(screen.getByRole("button", { name: "Make default" }));

    fireEvent.click(screen.getByTestId("team-save"));
    await waitFor(() => expect(saveCollabSettings).toHaveBeenCalled());
    const sent = saveCollabSettings.mock.calls[0][1] as CollabSettings;
    expect(sent.enabled).toBe(true);
    expect(sent.members[0]).toMatchObject({ name: "Marc", model_preset: "fast", skills: ["git", "seo-audit"] });
    expect(sent.teams).toEqual([
      { id: "dev", name: "Dev", members: ["mark", "salma"] },
      { id: "new-team", name: "New team", members: ["salma"] },
    ]);
    expect(sent.default_team).toBe("new-team");
  });
});
