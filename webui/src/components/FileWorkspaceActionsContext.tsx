// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { createContext, useContext, type ReactNode } from "react";

export type FileWorkspaceActions = {
  sessionKey: string;
  token: string;
  projectPath?: string | null;
  onOpenPreview: (path: string) => void;
  /**
   * Turns the path a chip carries into the path the agent actually wrote
   * (from this thread's file edits), so download and preview target the same
   * file. Returns the input unchanged when nothing in the thread matches.
   */
  resolvePath?: (path: string) => string;
};

const FileWorkspaceActionsContext = createContext<FileWorkspaceActions | null>(null);

export function FileWorkspaceActionsProvider({
  children,
  value,
}: {
  children: ReactNode;
  value: FileWorkspaceActions | null;
}) {
  return (
    <FileWorkspaceActionsContext.Provider value={value}>
      {children}
    </FileWorkspaceActionsContext.Provider>
  );
}

export function useFileWorkspaceActions(): FileWorkspaceActions | null {
  return useContext(FileWorkspaceActionsContext);
}
