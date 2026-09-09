export interface DocumentGeneration {
  mode: "model" | "deterministic";
  status: "complete" | "needs_review";
  warnings?: string[];
  skills?: {
    module?: string;
    action?: string;
    loaded?: string[];
    unavailable?: { name: string; status: string; reason: string }[];
  };
}
