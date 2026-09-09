// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useEffect, useMemo, useRef, useState } from "react";
import { Eye, FolderOpen, Loader2, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  discoverWorkspaceSkills,
  importWorkspaceSkills,
  installPluginPack,
} from "@/lib/api";
import { readLastDevContext } from "@/lib/last-dev-context";
import { nativeFolderPickerAvailable, pickNativeFolder } from "@/lib/native-dialog";
import { notifySkillsChanged } from "@/lib/skill-events";
import type {
  PluginPackSummary,
  SkillPreviewPayload,
  WorkspaceSkillCandidate,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

export type SkillInstallSource = "git" | "npx" | "upload" | "workspace";

const SKIP_UPLOAD_DIRS = new Set([
  ".git",
  "node_modules",
  "__pycache__",
  ".venv",
  "dist",
]);

export function InstallSkillDialog({
  open,
  onOpenChange,
  onInstalled,
  projectPath = null,
  variant = "dialog",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInstalled: (payload: { plugins: PluginPackSummary[] }) => void | Promise<void>;
  projectPath?: string | null;
  variant?: "dialog" | "panel";
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const folderInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<SkillInstallSource>("git");
  const [location, setLocation] = useState("");
  const [name, setName] = useState("");
  const [folderLabel, setFolderLabel] = useState("");
  const [pickedPath, setPickedPath] = useState("");
  const [archive, setArchive] = useState<{ filename: string; archiveBase64: string } | null>(
    null,
  );
  const [files, setFiles] = useState<Array<{ path: string; contentBase64: string }>>([]);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workspaceSkills, setWorkspaceSkills] = useState<WorkspaceSkillCandidate[]>([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [rawPath, setRawPath] = useState("");
  const [preview, setPreview] = useState<SkillPreviewPayload | null>(null);
  const [installedPreviews, setInstalledPreviews] = useState<SkillPreviewPayload[]>([]);
  const detectedPath = projectPath?.trim() || readLastDevContext()?.projectPath || "";
  const scanRoot = rawPath.trim() || detectedPath;

  useEffect(() => {
    if (!open) return;
    setRawPath(projectPath?.trim() || readLastDevContext()?.projectPath || "");
  }, [open, projectPath]);

  useEffect(() => {
    if (open) return;
    setLocation("");
    setName("");
    setFolderLabel("");
    setPickedPath("");
    setArchive(null);
    setFiles([]);
    setError(null);
    setInstalling(false);
    setWorkspaceSkills([]);
    setSelectedNames([]);
    setWorkspaceLoading(false);
    setPreview(null);
    setInstalledPreviews([]);
  }, [open]);

  useEffect(() => {
    if (!open || source !== "workspace" || !token) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setWorkspaceLoading(true);
      setError(null);
      discoverWorkspaceSkills(token, scanRoot || null, "", "workspace")
        .then((payload) => {
          if (cancelled) return;
          setWorkspaceSkills(payload.skills);
          setSelectedNames(
            payload.skills.filter((skill) => !skill.already).map((skill) => skill.name),
          );
        })
        .catch((err) => {
          if (cancelled) return;
          setWorkspaceSkills([]);
          setSelectedNames([]);
          const message = err instanceof Error ? err.message : String(err);
          setError(
            message === "skill not found"
              ? t("settings.plugins.discoverUnavailable", {
                  defaultValue:
                    "The gateway does not know this route yet. Restart it, then paste the project path below.",
                })
              : message,
          );
        })
        .finally(() => {
          if (!cancelled) setWorkspaceLoading(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, scanRoot, source, t, token]);

  const pendingWorkspace = useMemo(
    () => workspaceSkills.filter((skill) => !skill.already),
    [workspaceSkills],
  );

  const uploadPreviews = useMemo(() => previewsFromUploadedFiles(files), [files]);

  const previewTarget = useMemo((): SkillPreviewPayload | null => {
    if (preview) return preview;
    if (source === "workspace") {
      const name = selectedNames[0];
      const skill = workspaceSkills.find((item) => item.name === name);
      if (!skill) return null;
      return {
        name: skill.name,
        description: skill.description,
        markdown: skill.markdown || "",
        origin: skill.origin,
      };
    }
    return uploadPreviews[0] ?? installedPreviews[0] ?? null;
  }, [installedPreviews, preview, selectedNames, source, uploadPreviews, workspaceSkills]);

  const canSubmit =
    !scanRoot
      ? false
      : source === "workspace"
        ? selectedNames.length > 0
        : source === "git" || source === "npx"
          ? location.trim().length > 0
          : pickedPath.length > 0 || files.length > 0 || archive !== null;

  const submit = async () => {
    if (!token || !canSubmit) return;
    setInstalling(true);
    setError(null);
    try {
      if (source === "workspace") {
        const payload = await importWorkspaceSkills(token, {
          projectPath: scanRoot || null,
          names: selectedNames,
          scope: "workspace",
        });
        notifySkillsChanged();
        const nextPreviews = payload.previews ?? [];
        setInstalledPreviews(nextPreviews);
        if (nextPreviews[0]) setPreview(nextPreviews[0]);
        await onInstalled(payload);
        return;
      }
      const payload =
        source === "upload"
          ? await installPluginPack(token, {
              source: pickedPath ? "path" : "upload",
              location: pickedPath || undefined,
              name: name.trim() || undefined,
              filename: archive?.filename,
              archiveBase64: archive?.archiveBase64,
              files: pickedPath ? undefined : files.length ? files : undefined,
              scope: "workspace",
              projectPath: scanRoot || null,
            })
          : await installPluginPack(token, {
              source,
              location: location.trim(),
              name: name.trim() || undefined,
              scope: "workspace",
              projectPath: scanRoot || null,
            });
      notifySkillsChanged();
      const nextPreviews = payload.previews ?? [];
      setInstalledPreviews(nextPreviews);
      if (nextPreviews[0]) setPreview(nextPreviews[0]);
      await onInstalled(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(false);
    }
  };

  const pickLocalFolder = async () => {
    setError(null);
    if (nativeFolderPickerAvailable()) {
      try {
        const picked = await pickNativeFolder();
        if (!picked) return;
        setPickedPath(picked);
        setFolderLabel(picked);
        setFiles([]);
        setArchive(null);
        return;
      } catch {
        // Browser / refused bridge: fall through to the file picker.
      }
    }
    folderInputRef.current?.click();
  };

  const onFolderPicked = async (list: FileList | null) => {
    if (!list?.length) return;
    try {
      const next = await filesFromFolderList(list);
      if (!next.length) {
        setError(
          t("settings.plugins.uploadEmpty", {
            defaultValue: "This folder has no skill files to upload.",
          }),
        );
        return;
      }
      setFiles(next);
      setPickedPath("");
      setArchive(null);
      setFolderLabel(folderLabelFromFiles(next));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const onZipPicked = async (file: File | null) => {
    if (!file) return;
    try {
      const archiveBase64 = await fileToBase64(file);
      setArchive({ filename: file.name, archiveBase64 });
      setFiles([]);
      setPickedPath("");
      setFolderLabel(file.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const sourceTabs = (
    <div className="flex h-10 items-end gap-0" role="tablist" aria-label="Install source">
      {(
        [
          ["git", t("settings.plugins.sourceGit", { defaultValue: "Git" })],
          ["npx", t("settings.plugins.sourceNpx", { defaultValue: "NPX" })],
          ["upload", t("settings.plugins.sourceUpload", { defaultValue: "Local folder" })],
          ["workspace", t("settings.plugins.sourceWorkspace", { defaultValue: "Workspace" })],
        ] as const
      ).map(([id, label]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={source === id}
          onClick={() => {
            setSource(id);
            setError(null);
          }}
          className={cn(
            "h-10 shrink-0 border-b-2 px-3.5 text-[13px] font-medium transition-colors",
            source === id
              ? "border-foreground text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );

  const tabRow = sourceTabs;

  const actionButtons = (
    <>
      <Button
        type="button"
        variant="ghost"
        onClick={() => onOpenChange(false)}
        disabled={installing}
      >
        {installedPreviews.length
          ? t("common.done", { defaultValue: "Done" })
          : t("common.cancel", { defaultValue: "Cancel" })}
      </Button>
      <Button
        type="button"
        variant="outline"
        onClick={() => {
          if (previewTarget) setPreview(previewTarget);
        }}
        disabled={!previewTarget || (!previewTarget.markdown && !previewTarget.description)}
      >
        <Eye className="mr-1.5 h-4 w-4" aria-hidden />
        {t("settings.plugins.preview", { defaultValue: "Preview" })}
      </Button>
      <Button type="button" onClick={() => void submit()} disabled={installing || !canSubmit}>
        {installing ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" aria-hidden /> : null}
        {source === "workspace"
          ? t("settings.plugins.workspaceAdd", { defaultValue: "Add selected" })
          : t("settings.plugins.installConfirm", { defaultValue: "Install" })}
      </Button>
    </>
  );

  const formFields = (
    <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-[12px] font-medium text-foreground" htmlFor="install-skill-path">
              {t("settings.plugins.pathLabel", { defaultValue: "Project path" })}
            </label>
            <Input
              id="install-skill-path"
              value={rawPath}
              onChange={(event) => {
                setRawPath(event.target.value);
                setError(null);
              }}
              placeholder={t("settings.plugins.pathPlaceholder", {
                defaultValue: "C:\\Users\\me\\project  or  /home/me/project",
              })}
              className="h-10 rounded-xl font-mono text-[12.5px]"
              spellCheck={false}
              autoComplete="off"
            />
          </div>
          {!scanRoot ? (
            <p className="rounded-xl bg-destructive/10 px-3 py-2 text-[12.5px] text-destructive">
              {t("settings.plugins.scopeNeedProject", {
                defaultValue: "Open a project in Code first.",
              })}
            </p>
          ) : null}

          {source === "git" ? (
            <Input
              value={location}
              onChange={(event) => setLocation(event.target.value)}
              placeholder="https://github.com/org/my-skill.git"
              className="h-10 rounded-xl"
              autoFocus
            />
          ) : null}

          {source === "npx" ? (
            <Input
              value={location}
              onChange={(event) => setLocation(event.target.value)}
              placeholder="@org/navin-skill or my-skill@latest"
              className="h-10 rounded-xl"
              autoFocus
            />
          ) : null}

          {source === "upload" ? (
            <div className="space-y-2">
              <input
                ref={folderInputRef}
                type="file"
                className="hidden"
                multiple
                // @ts-expect-error webkitdirectory is not in the React types
                webkitdirectory=""
                directory=""
                onChange={(event) => void onFolderPicked(event.target.files)}
              />
              <input
                ref={zipInputRef}
                type="file"
                accept=".zip,.tgz,.tar.gz"
                className="hidden"
                onChange={(event) => void onZipPicked(event.target.files?.[0] ?? null)}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" className="h-10 rounded-xl" onClick={() => void pickLocalFolder()}>
                  <FolderOpen className="mr-1.5 h-4 w-4" aria-hidden />
                  {t("settings.plugins.chooseFolder", { defaultValue: "Choose folder" })}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="h-10 rounded-xl"
                  onClick={() => zipInputRef.current?.click()}
                >
                  <Upload className="mr-1.5 h-4 w-4" aria-hidden />
                  {t("settings.plugins.chooseZip", { defaultValue: "Upload zip" })}
                </Button>
              </div>
              <Input
                value={pickedPath}
                onChange={(event) => {
                  const next = event.target.value;
                  setPickedPath(next);
                  setFolderLabel(next);
                  setFiles([]);
                  setArchive(null);
                  setError(null);
                }}
                placeholder={t("settings.plugins.sourcePathPlaceholder", {
                  defaultValue: "C:\\Users\\me\\my-skill  or  /home/me/my-skill",
                })}
                className="h-10 rounded-xl font-mono text-[12.5px]"
                spellCheck={false}
                autoComplete="off"
              />
              {folderLabel && folderLabel !== pickedPath ? (
                <p className="truncate rounded-xl bg-muted/50 px-3 py-2 text-[12.5px] text-foreground">
                  {folderLabel}
                </p>
              ) : null}
            </div>
          ) : null}

          {source === "workspace" ? (
            <div className="space-y-2">
              {workspaceLoading ? (
                <div className="flex items-center gap-2 px-1 py-2 text-[12.5px] text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                </div>
              ) : workspaceSkills.length === 0 ? null : (
                <ul className="max-h-56 space-y-1 overflow-y-auto rounded-xl border border-border/50 p-1.5">
                  {workspaceSkills.map((skill) => {
                    const checked = selectedNames.includes(skill.name);
                    return (
                      <li key={`${skill.origin}:${skill.name}`}>
                        <label
                          className={cn(
                            "flex cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5",
                            skill.already
                              ? "cursor-default opacity-60"
                              : "hover:bg-muted/60",
                          )}
                        >
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={skill.already || checked}
                            disabled={skill.already}
                            onChange={() => {
                              setSelectedNames((current) =>
                                current.includes(skill.name)
                                  ? current.filter((item) => item !== skill.name)
                                  : [...current, skill.name],
                              );
                            }}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-[12.5px] font-medium text-foreground">
                              {skill.name}
                            </span>
                            <span className="block truncate text-[11px] text-muted-foreground">
                              {skill.already
                                ? t("settings.plugins.workspaceAlready", {
                                    defaultValue: "Already added · {{origin}}",
                                    origin: skill.origin,
                                  })
                                : skill.origin}
                            </span>
                          </span>
                          <button
                            type="button"
                            className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                            title={t("settings.plugins.preview", { defaultValue: "Preview" })}
                            aria-label={t("settings.plugins.preview", { defaultValue: "Preview" })}
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              setPreview({
                                name: skill.name,
                                description: skill.description,
                                markdown: skill.markdown || "",
                                origin: skill.origin,
                              });
                            }}
                          >
                            <Eye className="h-3.5 w-3.5" aria-hidden />
                          </button>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
              {pendingWorkspace.length > 1 ? (
                <button
                  type="button"
                  className="text-[12px] text-muted-foreground hover:text-foreground"
                  onClick={() =>
                    setSelectedNames(
                      selectedNames.length === pendingWorkspace.length
                        ? []
                        : pendingWorkspace.map((skill) => skill.name),
                    )
                  }
                >
                  {selectedNames.length === pendingWorkspace.length
                    ? t("settings.plugins.workspaceClear", { defaultValue: "Clear selection" })
                    : t("settings.plugins.workspaceSelectAll", { defaultValue: "Select all" })}
                </button>
              ) : null}
            </div>
          ) : null}

          {source === "workspace" ? null : (
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t("settings.plugins.namePlaceholder", {
              defaultValue: "Custom name (optional)",
            })}
            className="h-10 rounded-xl"
          />
          )}
          {error ? (
            <p className="rounded-xl bg-destructive/10 px-3 py-2 text-[12.5px] text-destructive">
              {error}
            </p>
          ) : null}
          {installedPreviews.length > 0 ? (
            <div className="flex flex-wrap items-center gap-2 rounded-xl bg-muted/50 px-3 py-2">
              {installedPreviews.map((item) => (
                <Button
                  key={item.name}
                  type="button"
                  variant="outline"
                  className="h-8 rounded-lg px-2.5 text-[12px]"
                  onClick={() => setPreview(item)}
                >
                  <Eye className="mr-1 h-3.5 w-3.5" aria-hidden />
                  {item.name}
                </Button>
              ))}
            </div>
          ) : null}
    </div>
  );

  return (
    <>
    {variant === "dialog" ? (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>
            {t("settings.plugins.installTitle", { defaultValue: "Install skill" })}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          {tabRow}
          {formFields}
        </div>
        <DialogFooter>
          {actionButtons}
        </DialogFooter>
      </DialogContent>
    </Dialog>
    ) : (
      <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="dev-install-skill-panel">
        <div className="flex h-10 shrink-0 items-center border-b border-border/55 px-3">
          {sourceTabs}
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 p-4">
            {formFields}
          </div>
        </div>
        <div className="flex shrink-0 items-center justify-end gap-2 border-t border-border/55 px-3 py-2">
          {actionButtons}
        </div>
      </div>
    )}
    <SkillPreviewDialog
      preview={preview}
      onClose={() => setPreview(null)}
    />
  </>
  );
}

function SkillPreviewDialog({
  preview,
  onClose,
}: {
  preview: SkillPreviewPayload | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  if (!preview) return null;
  return (
    <Dialog open onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>{preview.name}</DialogTitle>
          <DialogDescription>{preview.description}</DialogDescription>
        </DialogHeader>
        {preview.origin ? (
          <p className="truncate text-[12px] text-muted-foreground">{preview.origin}</p>
        ) : null}
        <pre className="max-h-[22rem] overflow-auto whitespace-pre-wrap rounded-xl bg-muted/50 p-3 text-[12.5px] leading-5 text-foreground">
          {preview.markdown || t("settings.plugins.previewEmpty", { defaultValue: "No SKILL.md body." })}
        </pre>
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            {t("common.close", { defaultValue: "Close" })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function previewsFromUploadedFiles(
  files: Array<{ path: string; contentBase64: string }>,
): SkillPreviewPayload[] {
  const previews: SkillPreviewPayload[] = [];
  for (const file of files) {
    const normalized = file.path.replace(/\\/g, "/");
    if (!normalized.toLowerCase().endsWith("skill.md")) continue;
    let markdown: string;
    try {
      markdown = atob(file.contentBase64);
    } catch {
      continue;
    }
    const nameMatch = /^name:\s*(.+)$/m.exec(markdown);
    const descMatch = /^description:\s*(.+)$/m.exec(markdown);
    const fallback = normalized.split("/").filter(Boolean).at(-2) || "skill";
    previews.push({
      name: (nameMatch?.[1] || fallback).trim(),
      description: (descMatch?.[1] || fallback).trim(),
      markdown,
      origin: normalized,
    });
  }
  return previews;
}

export async function filesFromFolderList(
  list: FileList,
): Promise<Array<{ path: string; contentBase64: string }>> {
  const rows: Array<{ path: string; contentBase64: string }> = [];
  let total = 0;
  for (const file of Array.from(list)) {
    const rel = (file.webkitRelativePath || file.name).replace(/\\/g, "/");
    const parts = rel.split("/").filter(Boolean);
    if (parts.some((part) => SKIP_UPLOAD_DIRS.has(part))) continue;
    if (file.size > 1_500_000) continue;
    total += file.size;
    if (total > 6_000_000) {
      throw new Error("This folder is too large to upload (max 6 MB).");
    }
    rows.push({
      path: parts.length > 1 ? parts.slice(1).join("/") || file.name : file.name,
      contentBase64: await fileToBase64(file),
    });
  }
  return rows;
}

function folderLabelFromFiles(files: Array<{ path: string }>): string {
  const first = files[0]?.path ?? "";
  const root = first.split("/")[0];
  return root ? `${root} (${files.length} files)` : `${files.length} files`;
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Could not read the selected file"));
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}
