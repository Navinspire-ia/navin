export type CliRunStatus = "running" | "done" | "error";
export type McpRunStatus = "running" | "done" | "error";

export interface CliRunSummary {
  key: string;
  name: string;
  args: string[];
  json: boolean;
  workingDir?: string;
  status: CliRunStatus;
  error?: string;
}

export interface McpRunSummary {
  key: string;
  presetName: string;
  displayName: string;
  toolName: string;
  argsPreview: string;
  status: McpRunStatus;
  error?: string;
}
