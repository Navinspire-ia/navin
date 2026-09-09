// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";

import i18n from "@/i18n";
import { AgentActivityCluster } from "@/components/thread/AgentActivityCluster";
import { MarkdownText } from "@/components/MarkdownText";
import type { UIMessage } from "@/lib/types";

const T0 = Date.now() - 60_000;

function trace(id: string, offsetMs: number, name: string, args: unknown, result: string, streaming = false): UIMessage {
  const createdAt = T0 + offsetMs;
  const call = `${name}(${JSON.stringify(args)})`;
  return {
    id,
    role: "assistant",
    kind: "trace",
    content: call,
    traces: [call],
    createdAt,
    turnSeq: offsetMs,
    toolEvents: [
      { phase: "start", call_id: id, name, arguments: args, client_started_at: createdAt },
      ...(streaming
        ? []
        : [{ phase: "end", call_id: id, name, arguments: args, result, client_ended_at: createdAt + 1200 }]),
    ],
  } as UIMessage;
}

function reasoning(id: string, offsetMs: number, text: string, streaming = false): UIMessage {
  return {
    id,
    role: "assistant",
    content: "",
    createdAt: T0 + offsetMs,
    turnSeq: offsetMs,
    reasoning: text,
    reasoningStreaming: streaming,
  } as UIMessage;
}

function liveMessages(): UIMessage[] {
  return [
    reasoning("r1", 0, "The user wants a folding activity block. I need to check the cluster, then the journal, then wire the motion."),
    trace("t1", 2_000, "glob", { pattern: "**/AgentActivityCluster.tsx" }, "1 match"),
    trace("t2", 4_000, "read_file", { path: "webui/src/components/thread/AgentActivityCluster.tsx" }, "1538 lines"),
    trace("t3", 7_000, "grep", { pattern: "isLatestTurn", path: "webui/src" }, "3 matches"),
    trace("t4", 10_000, "exec", { command: "npx vitest run activity-highlights" }, "23 passed", true),
  ];
}

function doneMessages(): UIMessage[] {
  const live = liveMessages();
  const last = live[live.length - 1];
  last.toolEvents = [
    ...(last.toolEvents ?? []).slice(0, 1),
    { phase: "end", call_id: last.id, name: "exec", arguments: { command: "npx vitest run activity-highlights" }, result: "23 passed", client_ended_at: T0 + 14_000 },
  ];
  return live;
}

const ANSWER = `## Repli terminé

Le bloc d'activité se **replie tout seul** une fois le tour terminé :

- l'en-tête garde la durée, le résumé coloré et les compteurs \`+/-\`
- le journal complet reste **à un clic**
- le repli est animé (ressort, sans rebond)`;

function Harness() {
  const [streaming, setStreaming] = useState(true);
  const [answer, setAnswer] = useState(false);
  useEffect(() => {
    (window as unknown as { __fold: unknown }).__fold = {
      finish: () => {
        setAnswer(true);
        setStreaming(false);
      },
      restart: () => {
        setAnswer(false);
        setStreaming(true);
      },
    };
  }, []);
  const messages = streaming ? liveMessages() : doneMessages();
  return (
    <div className="mx-auto flex max-w-[720px] flex-col gap-2 p-6" data-testid="fold-harness">
      <div className="self-end rounded-2xl bg-foreground/[0.06] px-3.5 py-2 text-[14px]">
        une fois terminé mets en mode cachette
      </div>
      <AgentActivityCluster
        messages={messages}
        isTurnStreaming={streaming}
        hasBodyBelow={answer}
        startedAtMs={T0}
        endedAtMs={streaming ? undefined : T0 + 14_000}
        turnLatencyMs={streaming ? undefined : 14_000}
        liveTaskHint={{ modelLabel: "deepseek-v4-flash" }}
        isLatestTurn
      />
      {answer ? (
        <div className="markdown-content text-[length:var(--chat-font-size)] leading-[var(--chat-line-height)]">
          <MarkdownText>{ANSWER}</MarkdownText>
        </div>
      ) : null}
      <div className="self-end rounded-2xl bg-foreground/[0.06] px-3.5 py-2 text-[14px]">
        et les fichiers modifiés ?
      </div>
      <AgentActivityCluster
        messages={[
          {
            id: "f1",
            role: "assistant",
            kind: "trace",
            content: "edit_file",
            traces: ["edit_file"],
            createdAt: T0 + 20_000,
            turnSeq: 20_000,
            fileEdits: [
              { call_id: "f1", tool: "edit_file", path: "webui/src/components/thread/AgentActivityCluster.tsx", added: 96, deleted: 41, status: streaming ? "editing" : "done", phase: streaming ? "start" : "end" },
              { call_id: "f2", tool: "edit_file", path: "webui/src/components/thread/activity-highlights.test.ts", added: 8, deleted: 1, status: "done", phase: "end" },
            ],
          } as UIMessage,
        ]}
        isTurnStreaming={streaming}
        hasBodyBelow={answer}
        startedAtMs={T0 + 20_000}
        endedAtMs={streaming ? undefined : T0 + 26_000}
        turnLatencyMs={streaming ? undefined : 6_000}
        isLatestTurn
      />
      {answer ? (
        <div className="markdown-content text-[length:var(--chat-font-size)] leading-[var(--chat-line-height)]">
          <MarkdownText>{"Deux fichiers touchés, le détail est replié dans l'en-tête."}</MarkdownText>
        </div>
      ) : null}
    </div>
  );
}

export function mount(dark = true) {
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.setAttribute("data-chat-density", "comfortable");
  let host = document.getElementById("__fold_harness");
  if (!host) {
    host = document.createElement("div");
    host.id = "__fold_harness";
    host.style.cssText = "position:fixed;inset:0;z-index:99999;overflow:auto;background:hsl(var(--background));color:hsl(var(--foreground))";
    document.body.appendChild(host);
  }
  createRoot(host).render(
    <StrictMode>
      <I18nextProvider i18n={i18n}>
        <Harness />
      </I18nextProvider>
    </StrictMode>,
  );
}
