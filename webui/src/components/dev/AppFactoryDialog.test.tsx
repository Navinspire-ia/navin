// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown> & { defaultValue?: string }) =>
      (options?.defaultValue ?? key).replace(/\{\{(\w+)\}\}/g, (_, name) => String(options?.[name] ?? "")),
  }),
}));

import { AppFactoryConfirmDialog, AppFactoryDialog } from "./AppFactoryDialog";

afterEach(cleanup);

const setup = (initialKind: "mobile" | "desktop" | null = null) => {
  const onStart = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <AppFactoryDialog open onOpenChange={onOpenChange} initialKind={initialKind} onStart={onStart} />,
  );
  return { onStart, onOpenChange };
};

describe("AppFactoryDialog", () => {
  it("renders 7 targets with their platform marks and 5 steps without technology names", () => {
    setup();
    const targets = screen.getAllByTestId(/^app-factory-target-/);
    expect(targets).toHaveLength(7);
    expect(targets.map((el) => el.querySelector("svg[data-platform]")?.getAttribute("data-platform"))).toEqual([
      "android", "ios", "windows", "macos", "ubuntu", "centos", "appimage",
    ]);
    expect(screen.getByTestId("app-factory-steps").querySelectorAll("li")).toHaveLength(5);
    const text = screen.getByTestId("app-factory-dialog").textContent ?? "";
    for (const word of ["Tauri", "Capacitor", "Rust", "Gradle"]) expect(text).not.toContain(word);
  });

  it("hands the picked target over for confirmation and closes", () => {
    const { onStart, onOpenChange } = setup();
    fireEvent.click(screen.getByTestId("app-factory-target-android"));
    expect(onStart).toHaveBeenCalledWith("android");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("lists desktop first when asked", () => {
    setup("desktop");
    const groups = screen.getAllByTestId(/^app-factory-group-/);
    expect(groups[0].getAttribute("data-testid")).toBe("app-factory-group-desktop");
  });
});

describe("AppFactoryConfirmDialog", () => {
  const confirm = (targetId: string | null, projectName: string | null = "shop") => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const view = render(
      <AppFactoryConfirmDialog targetId={targetId} projectName={projectName} onCancel={onCancel} onConfirm={onConfirm} />,
    );
    return { onCancel, onConfirm, view };
  };

  it("asks before creating the Android app of the project", () => {
    const { onConfirm, onCancel } = confirm("android");
    const dialog = screen.getByTestId("app-factory-confirm");
    expect(dialog.textContent).toContain("Create the Android app of your project?");
    expect(dialog.textContent).toContain("os/android");
    expect(dialog.textContent).toContain(".apk / .aab");
    expect(screen.getByTestId("app-factory-confirm-project").textContent).toBe("shop");
    expect(dialog.querySelector('svg[data-platform="android"]')).not.toBeNull();
    for (const word of ["Tauri", "Capacitor", "Rust", "Gradle"]) expect(dialog.textContent).not.toContain(word);
    const start = screen.getByTestId("app-factory-confirm-start");
    expect(start.textContent).toBe("Create the Android app");
    fireEvent.click(start);
    expect(onConfirm).toHaveBeenCalledWith("android");
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("cancels without starting anything", () => {
    const { onConfirm, onCancel } = confirm("windows", null);
    expect(screen.getByTestId("app-factory-confirm").textContent).toContain("Create the Windows app of your project?");
    expect(screen.queryByTestId("app-factory-confirm-project")).toBeNull();
    fireEvent.click(screen.getByTestId("app-factory-confirm-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("stays closed without a target or with an unknown one", () => {
    confirm(null);
    expect(screen.queryByTestId("app-factory-confirm")).toBeNull();
    cleanup();
    confirm("electron");
    expect(screen.queryByTestId("app-factory-confirm")).toBeNull();
  });
});
