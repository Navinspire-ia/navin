// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CollabPayload, CollabSettings, TeamMember } from "@/lib/team";
import "@/i18n";

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
const fetchTeammateSpeech = vi.fn(async (_token: string, value: { voice: string }) => ({
  audio_base64: "SUQz", mime: "audio/mpeg", voice: value.voice || "Matthew",
}));

vi.mock("@/lib/api", () => ({
  fetchCollabSettings: vi.fn(async () => payload()),
  saveCollabSettings: (token: string, values: unknown) => saveCollabSettings(token, values),
  setCollabEnabled: (token: string, enabled: boolean) => setCollabEnabled(token, enabled),
  fetchTeammateSpeech: (token: string, value: { voice: string }) => fetchTeammateSpeech(token, value),
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
    // The availability switch sits beside the row button, never inside it, and
    // switching a teammate off leaves the selected teammate in the editor.
    expect(document.querySelector("button button")).toBeNull();
    fireEvent.click(screen.getByRole("switch", { name: "Salma available" }));
    expect(screen.getByRole("switch", { name: "Salma available" }).getAttribute("aria-checked")).toBe("false");
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
    expect(sent.members[1]).toMatchObject({ id: "salma", enabled: false });
    expect(sent.teams).toEqual([
      { id: "dev", name: "Dev", members: ["mark", "salma"] },
      { id: "new-team", name: "New team", members: ["salma"] },
    ]);
    expect(sent.default_team).toBe("new-team");
  });

  it("gives a teammate a photo, a gender and a voice the user can hear before saving", async () => {
    saveCollabSettings.mockClear();
    const played: string[] = [];
    vi.stubGlobal("Audio", class {
      onended: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public src: string) {}
      play() { played.push(this.src); return Promise.resolve(); }
      pause() {}
    });
    vi.stubGlobal("createImageBitmap", async () => ({ width: 640, height: 480, close: () => {} }));
    const drawImage = vi.fn();
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
    const toDataURL = vi.spyOn(HTMLCanvasElement.prototype, "toDataURL").mockReturnValue("data:image/webp;base64,UklGRg==");
    try {
      render(<TeamSettings token="tok" />);
      await screen.findByTestId("team-settings");
      fireEvent.click(screen.getByRole("tab", { name: /Members/ }));

      fireEvent.change(screen.getByRole("combobox", { name: "Gender" }), { target: { value: "male" } });
      fireEvent.change(screen.getByRole("textbox", { name: "Live voice" }), { target: { value: "Remi" } });
      fireEvent.click(screen.getByRole("button", { name: "Listen" }));
      await screen.findByText("Voice: Remi");
      expect(fetchTeammateSpeech).toHaveBeenCalledWith("tok", expect.objectContaining({ key: "mark", gender: "male", voice: "Remi" }));
      expect(played).toEqual(["data:audio/mpeg;base64,SUQz"]);

      const file = new File([new Uint8Array([1, 2, 3])], "mark.png", { type: "image/png" });
      fireEvent.change(screen.getByTestId("teammate-photo-input"), { target: { files: [file] } });
      await waitFor(() => expect(screen.getAllByTestId("teammate-photo").length).toBeGreaterThan(0));
      // Cropped to the middle square, 256 px.
      expect(drawImage).toHaveBeenCalledWith(expect.anything(), 80, 0, 480, 480, 0, 0, 256, 256);

      fireEvent.click(screen.getByTestId("team-save"));
      await waitFor(() => expect(saveCollabSettings).toHaveBeenCalled());
      const sent = saveCollabSettings.mock.calls[0][1] as CollabSettings;
      expect(sent.members[0]).toMatchObject({
        name: "Mark", gender: "male", voice: "Remi", photo: "data:image/webp;base64,UklGRg==",
      });
    } finally {
      getContext.mockRestore();
      toDataURL.mockRestore();
      vi.unstubAllGlobals();
    }
  });
});
