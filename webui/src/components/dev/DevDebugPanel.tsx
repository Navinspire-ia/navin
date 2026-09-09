// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import {
  Bug,
  ChevronRight,
  Loader2,
  Play,
  SkipForward,
  Square,
  StepBack,
  StepForward,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { debugOp } from "@/lib/api";
import {
  getAllBreakpoints,
  getBreakpointLines,
  subscribeBreakpoints,
} from "@/lib/debug-breakpoints";
import type { DebugStackFrame, DebugStatePayload, DebugVariable } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const DEBUGGABLE_EXT = /\.(py|js|mjs|cjs|ts|mts|cts)$/i;

function isDebuggablePath(path: string | null | undefined): boolean {
  return Boolean(path && DEBUGGABLE_EXT.test(path));
}

function runtimeForPath(path: string | null | undefined): "python" | "node" | undefined {
  if (!path) return undefined;
  if (/\.(js|mjs|cjs|ts|mts|cts)$/i.test(path)) return "node";
  if (/\.pyw?$/i.test(path)) return "python";
  return undefined;
}

/**
 * DAP debugger panel: launch current Python/Node file / pytest node, step, stack, vars.
 */
export function DevDebugPanel({
  token,
  sessionKey,
  activePath,
  onOpenFrame,
  onClose,
  onState,
}: {
  token: string;
  sessionKey: string;
  activePath: string | null;
  onOpenFrame?: (path: string, line: number) => void;
  onClose?: () => void;
  onState?: (state: DebugStatePayload) => void;
}) {
  const { t } = useTranslation();
  const [state, setState] = useState<DebugStatePayload | null>(null);
  const [frames, setFrames] = useState<DebugStackFrame[]>([]);
  const [variables, setVariables] = useState<DebugVariable[]>([]);
  const [expr, setExpr] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pytestNode, setPytestNode] = useState("");
  const [, bump] = useState(0);

  useEffect(() => subscribeBreakpoints(() => bump((n) => n + 1)), []);

  const refresh = useCallback(async () => {
    try {
      const next = await debugOp(token, sessionKey, "state");
      setState(next);
      onState?.(next);
      if (next.state === "stopped") {
        const stack = await debugOp(token, sessionKey, "stack");
        setFrames(stack.frames ?? []);
        const top = stack.frames?.[0];
        if (top?.id != null) {
          const scopes = await debugOp(token, sessionKey, "scopes", {
            frameId: top.id,
          });
          const ref = scopes.scopes?.[0]?.variablesReference;
          if (ref) {
            const vars = await debugOp(token, sessionKey, "variables", {
              variablesReference: ref,
            });
            setVariables(vars.variables ?? []);
          }
        }
        if (top?.path && top.line) onOpenFrame?.(top.path, top.line);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [onOpenFrame, onState, sessionKey, token]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (state?.state === "running" || state?.state === "starting" || state?.state === "stopped") {
        void refresh();
      }
    }, 700);
    return () => window.clearInterval(timer);
  }, [refresh, state?.state]);

  const syncBreakpoints = async () => {
    for (const row of getAllBreakpoints()) {
      await debugOp(token, sessionKey, "setBreakpoints", {
        path: row.path,
        lines: row.lines,
      });
    }
  };

  const run = async (mode: "file" | "pytest") => {
    setBusy(true);
    setError(null);
    try {
      await syncBreakpoints();
      const runtime = runtimeForPath(activePath);
      const payload =
        mode === "pytest"
          ? await debugOp(token, sessionKey, "start", {
              pytest: pytestNode || activePath || "",
              runtime: "python",
            })
          : await debugOp(token, sessionKey, "start", {
              program: activePath || "",
              ...(runtime ? { runtime } : {}),
            });
      setState(payload);
      onState?.(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const act = async (op: string, params: Record<string, unknown> = {}) => {
    setBusy(true);
    setError(null);
    try {
      const payload = await debugOp(token, sessionKey, op, params);
      setState(payload);
      onState?.(payload);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const status = state?.state ?? "idle";
  const canStep = status === "stopped";
  const bpCount = getAllBreakpoints().reduce((n, row) => n + row.lines.length, 0);

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <div className="flex shrink-0 items-center gap-1 border-b border-border/40 px-2 py-1">
        <Bug className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        <span className="text-[11px] font-medium text-foreground">
          {t("dev.debug.title", { defaultValue: "Debug" })}
        </span>
        <span className="ml-1 rounded bg-muted px-1.5 py-0.5 font-mono text-[10.5px] text-muted-foreground">
          {status}
        </span>
        <span className="text-[10.5px] text-muted-foreground">
          {bpCount} {t("dev.debug.bps", { defaultValue: "bps" })}
        </span>
        <div className="ml-auto flex items-center gap-0.5">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-1.5"
            disabled={busy || !isDebuggablePath(activePath)}
            title={t("dev.debug.startFile", { defaultValue: "Debug current file" })}
            onClick={() => void run("file")}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-1.5"
            disabled={busy || !canStep}
            onClick={() => void act("continue")}
            title={t("dev.debug.continue", { defaultValue: "Continue" })}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-1.5"
            disabled={busy || !canStep}
            onClick={() => void act("step", { kind: "over" })}
            title={t("dev.debug.stepOver", { defaultValue: "Step over" })}
          >
            <StepForward className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-1.5"
            disabled={busy || !canStep}
            onClick={() => void act("step", { kind: "into" })}
            title={t("dev.debug.stepInto", { defaultValue: "Step into" })}
          >
            <SkipForward className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-1.5"
            disabled={busy || !canStep}
            onClick={() => void act("step", { kind: "out" })}
            title={t("dev.debug.stepOut", { defaultValue: "Step out" })}
          >
            <StepBack className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 px-1.5"
            disabled={busy || status === "idle"}
            onClick={() => void act("stop")}
            title={t("dev.debug.stop", { defaultValue: "Stop" })}
          >
            <Square className="h-3.5 w-3.5" />
          </Button>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              className="ml-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
            >
              {t("dev.debug.close", { defaultValue: "Close" })}
            </button>
          ) : null}
        </div>
      </div>

      <div className="flex shrink-0 gap-1 border-b border-border/40 px-2 py-1">
        <Input
          value={pytestNode}
          onChange={(event) => setPytestNode(event.target.value)}
          placeholder={t("dev.debug.pytestPlaceholder", {
            defaultValue: "pytest node (tests/test_x.py::test_y)",
          })}
          className="h-7 flex-1 text-[11px]"
        />
        <Button
          type="button"
          size="sm"
          className="h-7 px-2 text-[11px]"
          disabled={busy || !(pytestNode || activePath)}
          onClick={() => void run("pytest")}
        >
          {t("dev.debug.debugTest", { defaultValue: "Debug test" })}
        </Button>
      </div>

      {error ? (
        <p className="shrink-0 px-2 py-1 text-[11px] text-red-500">{error}</p>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-3 gap-0">
        <div className="min-h-0 overflow-y-auto border-r border-border/40 p-1.5">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("dev.debug.stack", { defaultValue: "Call stack" })}
          </p>
          {frames.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              {t("dev.debug.noFrames", { defaultValue: "No frames." })}
            </p>
          ) : (
            <ul className="space-y-0.5">
              {frames.map((frame) => (
                <li key={frame.id}>
                  <button
                    type="button"
                    onClick={() => onOpenFrame?.(frame.path, frame.line)}
                    className="block w-full truncate rounded px-1 py-0.5 text-left text-[11px] hover:bg-muted"
                    title={`${frame.path}:${frame.line}`}
                  >
                    <span className="text-foreground">{frame.name}</span>
                    <span className="ml-1 text-muted-foreground">
                      {frame.path}:{frame.line}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="min-h-0 overflow-y-auto border-r border-border/40 p-1.5">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("dev.debug.variables", { defaultValue: "Variables" })}
          </p>
          {variables.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              {t("dev.debug.noVars", { defaultValue: "No locals." })}
            </p>
          ) : (
            <ul className="space-y-0.5 font-mono text-[11px]">
              {variables.map((variable) => (
                <li key={variable.name} className="truncate px-1">
                  <span className="text-foreground">{variable.name}</span>
                  <span className="text-muted-foreground"> = {variable.value}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="mb-1 mt-3 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("dev.debug.breakpoints", { defaultValue: "Breakpoints" })}
            {activePath
              ? ` · ${getBreakpointLines(activePath).join(", ") || "-"}`
              : ""}
          </p>
          <ul className="space-y-0.5 text-[11px] text-muted-foreground">
            {getAllBreakpoints().map((row) => (
              <li key={row.path} className="truncate px-1 font-mono">
                {row.path}:{row.lines.join(",")}
              </li>
            ))}
          </ul>
        </div>

        <div className="flex min-h-0 flex-col p-1.5">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("dev.debug.console", { defaultValue: "Debug console" })}
          </p>
          <div className="min-h-0 flex-1 overflow-y-auto rounded border border-border/40 bg-muted/20 p-1 font-mono text-[11px]">
            {(state?.console ?? []).length === 0 ? (
              <p className="text-muted-foreground">
                {t("dev.debug.consoleEmpty", {
                  defaultValue: "Output and evaluate results appear here.",
                })}
              </p>
            ) : (
              (state?.console ?? []).map((line, index) => (
                <div key={`${index}-${line.slice(0, 12)}`} className={cn("whitespace-pre-wrap")}>
                  {line}
                </div>
              ))
            )}
          </div>
          <form
            className="mt-1 flex gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              if (!expr.trim()) return;
              void act("evaluate", { expression: expr }).then(() => setExpr(""));
            }}
          >
            <Input
              value={expr}
              onChange={(event) => setExpr(event.target.value)}
              disabled={!canStep}
              placeholder={t("dev.debug.evalPlaceholder", { defaultValue: "Evaluate..." })}
              className="h-7 flex-1 font-mono text-[11px]"
            />
            <Button type="submit" size="sm" className="h-7 px-2 text-[11px]" disabled={!canStep || busy}>
              {t("dev.debug.eval", { defaultValue: "Eval" })}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}

export default DevDebugPanel;
