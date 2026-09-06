import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Award,
  Ban,
  BookOpen,
  Brain,
  BrainCircuit,
  Compass,
  Download,
  Eye,
  EyeOff,
  FlaskConical,
  Gavel,
  Globe,
  History,
  Lightbulb,
  Loader2,
  Lock,
  Minus,
  PenLine,
  Play,
  Power,
  Radar,
  RefreshCw,
  Repeat,
  RotateCcw,
  Scale,
  ScrollText,
  Search,
  ShieldAlert,
  ShieldCheck,
  Snowflake,
  Sparkles,
  Target,
  Trash2,
  Upload,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  agiAction,
  fetchAgi,
  fetchAgiDraft,
  fetchCognition,
  fetchPolicy,
  fetchTransfer,
  fetchWorld,
  policyAction,
  transferAction,
  updateAgi,
  updateCognition,
  updatePolicy,
  updateTransfer,
  updateWorld,
  worldAction,
  type AgiAction,
  type AgiDraft,
  type AgiFields,
  type AgiState,
  type CognitionFields,
  type CognitionState,
  type PolicyAction,
  type PolicyFields,
  type PolicyState,
  type PolicyVerdict,
  type TransferAction,
  type TransferFamilyScore,
  type TransferFields,
  type TransferState,
  type WorldAction,
  type WorldFields,
  type WorldScore,
  type WorldState,
} from "@/lib/api";
import { bindMenuListWheel } from "@/lib/model-picker-scroll";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import { Card, GuardrailRow, ICON_TONE, PILL_TONE, Section, StatusPill } from "./DevGuardrailsPanel";

type AgiKey = keyof AgiFields;
type CognitionKey = keyof CognitionFields;
type WorldKey = keyof WorldFields;
type TransferKey = "enabled";

/** "0.98 -> 0.41" for a scored head; "-" before the first exam. */
export function worldScoreLabel(score: Pick<WorldScore, "baseline_log_loss" | "log_loss"> | null | undefined): string {
  if (!score || typeof score.log_loss !== "number" || typeof score.baseline_log_loss !== "number") return "-";
  return `${score.baseline_log_loss.toFixed(2)} -> ${score.log_loss.toFixed(2)}`;
}

/** "87%" for a probability, "-" when unknown. */
export function percentLabel(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 100)}%` : "-";
}

/**
 * The advise switch is a gate, not a preference: it can only go on when the
 * frozen exam says up and the offline A/B says gain. Off is always allowed.
 */
export function adviseSwitchLocked(state: Pick<WorldState, "enabled" | "advise" | "gate"> | null): boolean {
  if (!state || !state.enabled) return true;
  if (state.advise) return false;
  return !state.gate.open;
}

/**
 * Which world model buttons make sense right now. The engine already logs
 * and trains on its own; these are the human's "plus" buttons only.
 */
export function worldActions(state: Pick<WorldState, "enabled" | "rows" | "active" | "heldout" | "min_rows"> | null): WorldAction[] {
  if (!state || !state.enabled) return [];
  const actions: WorldAction[] = [];
  if (state.rows >= state.min_rows) actions.push("train");
  if (state.active) actions.push("exam", "ab", "beliefs", "rollback");
  if (state.rows >= state.min_rows) actions.push("freeze");
  return actions;
}

/** Freeze and roll back change what the scores mean: they are confirmed. */
export const CONFIRMED_WORLD_ACTIONS: ReadonlySet<WorldAction> = new Set<WorldAction>(["freeze", "rollback"]);

type PolicyKey = keyof PolicyFields;

/**
 * The policy master switch needs the world model radar (S3.3) up: no policy
 * without a radar. Off is always allowed.
 */
export function policySwitchLocked(state: Pick<PolicyState, "enabled" | "radar"> | null): boolean {
  if (!state) return true;
  if (state.enabled) return false;
  return !state.radar.up;
}

/**
 * The steer switch is a gate, not a preference: it can only go on when
 * adapter N+1 beat N with no suite down and the offline A/B says gain.
 */
export function steerSwitchLocked(state: Pick<PolicyState, "enabled" | "steer" | "gate"> | null): boolean {
  if (!state || !state.enabled) return true;
  if (state.steer) return false;
  return !state.gate.open;
}

/**
 * Which policy buttons make sense right now. The engine already collects and
 * trains on its own; these are the human's "plus" buttons only.
 */
export function policyActions(
  state: Pick<PolicyState, "enabled" | "radar" | "active" | "heldout" | "rows" | "checkpoints"> | null,
): PolicyAction[] {
  if (!state || !state.enabled) return [];
  const actions: PolicyAction[] = [];
  if (state.radar.up) actions.push("train");
  if (state.active) actions.push("exam", "ab", "publish", "rollback");
  if (state.rows > 0) actions.push("freeze");
  return actions;
}

/** The newest adapter a human may force: flat, not serving, not regressed. */
export function forceableAdapter(state: Pick<PolicyState, "checkpoints" | "active"> | null): number | null {
  if (!state) return null;
  const candidates = state.checkpoints.filter((item) => {
    if (item.active) return false;
    const verdict = item.verdict_vs_active ?? item.verdict_vs_baseline;
    return Boolean(verdict) && !verdict!.regressed && !verdict!.eligible;
  });
  return candidates.length ? candidates[candidates.length - 1].number : null;
}

/** Actions that change what the scores mean or leave the project: confirmed. */
export const CONFIRMED_POLICY_ACTIONS: ReadonlySet<PolicyAction> = new Set<PolicyAction>([
  "freeze",
  "rollback",
  "force",
  "publish",
  "adopt",
  "unpublish",
]);

/** "38% -> 62%" for a scored adapter; "-" before the first exam. */
export function policyScoreLabel(
  score: { baseline_accuracy: number | null; accuracy: number | null } | null | undefined,
): string {
  if (!score || typeof score.accuracy !== "number" || typeof score.baseline_accuracy !== "number") return "-";
  return `${percentLabel(score.baseline_accuracy)} -> ${percentLabel(score.accuracy)}`;
}

/** "code up / browser flat / desk up" for the per-suite verdicts. */
export function suiteVerdictsLabel(verdict: PolicyVerdict | null | undefined): string {
  if (!verdict) return "";
  return Object.entries(verdict.suites)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([suite, value]) => `${suite} ${value}`)
    .join(" / ");
}

/**
 * The transfer master switch needs S2 finished, S3.3 up and S4.3 up: a
 * campaign without them is a demo. Off is always allowed.
 */
export function transferSwitchLocked(state: Pick<TransferState, "enabled" | "prereqs"> | null): boolean {
  if (!state) return true;
  if (state.enabled) return false;
  return !state.prereqs.ok;
}

export type LadderStepId = "skills" | "world" | "policy" | "transfer";
export type LadderStepState = "off" | "on" | "locked";
export type LadderStep = { id: LadderStepId; state: LadderStepState; reasons: string[] };

/**
 * The ladder at the top of the panel: four switches that earn each other.
 * A locked switch is not broken; it turns clickable by itself once the stage
 * below has passed its exam. Computed from the states the panel already
 * holds so it renders even on a gateway without the /transfer route; the
 * gateway's own rungs (with their reasons) win when present.
 */
export function ladderSteps(input: {
  agi: Pick<AgiState, "enabled"> | null;
  world: Pick<WorldState, "enabled"> | null;
  policy: Pick<PolicyState, "enabled" | "radar"> | null;
  transfer: Pick<TransferState, "enabled" | "prereqs" | "ladder"> | null;
}): LadderStep[] {
  const fromGateway = input.transfer?.ladder;
  if (fromGateway && fromGateway.length === 4) {
    return fromGateway.map((r) => ({ id: r.id, state: r.state, reasons: r.reasons ?? [] }));
  }
  const state = (enabled: boolean | undefined, locked: boolean): LadderStepState =>
    enabled ? "on" : locked ? "locked" : "off";
  const policyLocked = input.policy ? policySwitchLocked(input.policy) : false;
  const transferLocked = input.transfer ? transferSwitchLocked(input.transfer) : false;
  return [
    { id: "skills", state: state(input.agi?.enabled, false), reasons: [] },
    { id: "world", state: state(input.world?.enabled, false), reasons: [] },
    { id: "policy", state: state(input.policy?.enabled, policyLocked), reasons: policyLocked ? (input.policy?.radar?.reasons ?? []) : [] },
    {
      id: "transfer",
      state: state(input.transfer?.enabled, transferLocked),
      reasons: transferLocked ? (input.transfer?.prereqs?.reasons ?? []) : [],
    },
  ];
}

/**
 * Which transfer buttons make sense right now. Nothing runs by itself: the
 * safety dossier and the kill drill are always pressable once on; the
 * campaign only with frozen, untouched suites outside every repository.
 * `freeze` lives in the CLI (it needs names on record).
 */
export function transferActions(
  state: Pick<TransferState, "enabled" | "prereqs" | "suites" | "campaign"> | null,
): TransferAction[] {
  if (!state || !state.enabled) return [];
  const actions: TransferAction[] = ["safety", "kill_drill"];
  const suites = state.suites;
  if (suites && suites.frozen) actions.push("verify");
  if (suites && suites.frozen && !suites.tampered && suites.outside && state.prereqs.ok) actions.push("campaign");
  if (state.campaign && state.campaign.id && suites && suites.frozen && !suites.tampered) actions.push("replay");
  return actions;
}

/** Actions that take hours or exercise the kill switches: confirmed. */
export const CONFIRMED_TRANSFER_ACTIONS: ReadonlySet<TransferAction> = new Set<TransferAction>([
  "campaign",
  "replay",
  "kill_drill",
]);

/** "code 100%/60% pass / browser 75%/60% pass / business 25%/60% fail" for one campaign. */
export function familyScoresLabel(families: Record<string, TransferFamilyScore> | null | undefined): string {
  if (!families) return "";
  return Object.entries(families)
    .map(([family, score]) => `${family} ${percentLabel(score.pass_rate)}/${percentLabel(score.bar)} ${score.verdict ?? "-"}`)
    .join(" / ");
}

/** Tone of a protocol answer: pass and discussable are up, fail / void / forbidden down, the rest flat. */
export function claimTone(value: string | null | undefined): "up" | "flat" | "down" {
  if (value === "pass" || value === "discussable") return "up";
  if (value === "fail" || value === "void" || value === "forbidden" || value === "collapse") return "down";
  return "flat";
}

/** "12 KB" for the journal size; empty below 1 KB so the row stays quiet at first. */
export function journalSizeLabel(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 1024) return "";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Recall is wanted but the serving agent does not have the tool yet: say it. */
export function recallRestartNoteVisible(state: CognitionState | null): boolean {
  return Boolean(state && state.enabled && state.recall && state.recall_requires_restart);
}

/** Recall is wanted and the gateway confirmed the agent has it right now. */
export function recallLiveNoteVisible(state: CognitionState | null): boolean {
  return Boolean(state && state.enabled && state.recall && state.recall_registered === true);
}

/** "4/20" for a scored draft, "-" before the first exam. */
export function scoreLabel(score: number | null | undefined): string {
  return typeof score === "number" && Number.isFinite(score) ? `${score}/20` : "-";
}

/**
 * Which buttons a draft offers, by status. The engine already did the
 * automatic part; these are the human's "plus" buttons only.
 */
export function draftActions(draft: AgiDraft, state: Pick<AgiState, "promote_project" | "publish_harness">): AgiAction[] {
  switch (draft.status) {
    case "eligible":
      return state.promote_project ? ["exam", "discard"] : ["promote", "exam", "discard"];
    case "flat":
    case "retired":
      return ["force", "exam", "discard"];
    case "promoted":
      return state.publish_harness ? ["publish", "exam", "rollback"] : ["exam", "rollback"];
    case "rejected":
    case "drafting":
      return ["exam", "discard"];
    case "examining":
      return [];
    default:
      return ["discard"];
  }
}

/** Publish and force need a human click on the gateway; discard and rollback are confirmed too. */
export const CONFIRMED_ACTIONS: ReadonlySet<AgiAction> = new Set<AgiAction>([
  "publish",
  "force",
  "discard",
  "rollback",
]);

const VERDICT_TONE: Record<"up" | "flat" | "down", string> = {
  up: "text-emerald-600 dark:text-emerald-400",
  flat: "text-muted-foreground",
  down: "text-red-600 dark:text-red-400",
};

const STATUS_PILL: Record<AgiDraft["status"], string> = {
  drafting: PILL_TONE.off,
  examining: PILL_TONE.blocked,
  eligible: PILL_TONE.on,
  flat: PILL_TONE.blocked,
  rejected: "bg-red-500/15 text-red-700 dark:text-red-300",
  promoted: PILL_TONE.on,
  retired: PILL_TONE.off,
  discarded: PILL_TONE.off,
};

function VerdictIcon({ verdict, className }: { verdict: "up" | "flat" | "down" | null; className?: string }) {
  if (verdict === "up") return <ArrowUpRight className={className} aria-hidden />;
  if (verdict === "down") return <ArrowDownRight className={className} aria-hidden />;
  return <Minus className={className} aria-hidden />;
}

function ActionButton({
  icon: Icon,
  label,
  onClick,
  disabled,
  tone = "ghost",
  testId,
}: {
  icon: typeof Play;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "ghost" | "primary" | "danger";
  testId: string;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={tone === "primary" ? "default" : "ghost"}
      className={cn(
        "h-7 gap-1.5 rounded-full px-2.5 text-[12px]",
        tone === "danger" && "text-destructive hover:text-destructive",
      )}
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {label}
    </Button>
  );
}

/**
 * AGI: what the agent may learn on its own, per project.
 *
 * Three families of switches, all off by default and all written one switch
 * at a time to a small file under `.navin/`: skills evolution (Navin drafts,
 * examines and promotes skills through a frozen exam, `.navin/skills-evolve.json`),
 * the world model (tool journal, offline head, gated advice,
 * `.navin/world-model.json`) and episodic memory (journal and recall tool,
 * `.navin/cognition.json`). Nothing here runs inside a chat turn: exams and
 * training happen after the turn, on the job runner, from `navin agi ...` or a
 * cron. Publishing a skill to every project and opening live advice stay human
 * decisions, the latter only once the exam and the A/B allow it.
 */
export function DevAgiPanel({
  sessionKey,
  onOpenSettings,
}: {
  sessionKey: string | null;
  /** Opens Settings > Security (restart button) for the recall note. */
  onOpenSettings?: () => void;
}) {
  const { token } = useClient();
  const { t } = useTranslation();
  const tx = useCallback(
    (key: string, fallback: string) => t(key, { defaultValue: fallback }),
    [t],
  );
  const reduced = useReducedMotion();

  const boardKey = sessionKey ?? "websocket:webui-dev";
  const [agi, setAgi] = useState<AgiState | null>(null);
  const [cognition, setCognition] = useState<CognitionState | null>(null);
  const [world, setWorld] = useState<WorldState | null>(null);
  const [worldJournalOpen, setWorldJournalOpen] = useState(false);
  const [worldConfirm, setWorldConfirm] = useState<WorldAction | null>(null);
  const [policy, setPolicy] = useState<PolicyState | null>(null);
  const [policyJournalOpen, setPolicyJournalOpen] = useState(false);
  const [policyConfirm, setPolicyConfirm] = useState<{ action: PolicyAction; name?: string; number?: number } | null>(null);
  const [transfer, setTransfer] = useState<TransferState | null>(null);
  const [transferJournalOpen, setTransferJournalOpen] = useState(false);
  const [transferConfirm, setTransferConfirm] = useState<{ action: TransferAction; name?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ name: string; markdown: string } | null>(null);
  const [journalOpen, setJournalOpen] = useState(false);
  const [newDraftOpen, setNewDraftOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [confirm, setConfirm] = useState<{ action: AgiAction; name: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const node = scrollRef.current;
    return node ? bindMenuListWheel(node) : undefined;
  });

  const load = useCallback(
    async (options?: { silent?: boolean }) => {
      if (!token) return;
      if (!options?.silent) setLoading(true);
      try {
        const [evolution, memory, worldModel, policyModel, transferModel] = await Promise.all([
          fetchAgi(token, boardKey),
          // Memory, world model, policy and transfer are sidecars: if a route is
          // missing the rest of the panel still answers, and that card shows as unavailable.
          fetchCognition(token, boardKey).catch(() => null),
          fetchWorld(token, boardKey).catch(() => null),
          fetchPolicy(token, boardKey).catch(() => null),
          fetchTransfer(token, boardKey).catch(() => null),
        ]);
        setAgi(evolution);
        setCognition(memory);
        setWorld(worldModel);
        setPolicy(policyModel);
        setTransfer(transferModel);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [token, boardKey],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // A job may be running on the gateway: poll quietly while there is one.
  useEffect(() => {
    if (!agi || agi.pending_jobs.length === 0) return;
    const timer = window.setInterval(() => void load({ silent: true }), 4000);
    return () => window.clearInterval(timer);
  }, [agi, load]);

  const saveAgi = useCallback(
    async (key: AgiKey, value: boolean) => {
      if (!token) return;
      setSaving(true);
      // One switch per write: the gateway merges it into
      // .navin/skills-evolve.json and answers with the whole state.
      const fields: AgiFields = { [key]: value };
      setAgi((prev) => (prev ? { ...prev, ...fields } : prev));
      try {
        const next = await updateAgi(token, boardKey, fields);
        setAgi(next);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setSaving(false);
      }
    },
    [token, boardKey, load],
  );

  const saveCognition = useCallback(
    async (key: CognitionKey, value: boolean) => {
      if (!token) return;
      setSaving(true);
      // One switch per write, like the autonomy card: the gateway merges it
      // into .navin/cognition.json and answers with the whole state.
      const fields: CognitionFields = { [key]: value };
      setCognition((prev) => (prev ? { ...prev, ...fields } : prev));
      try {
        const next = await updateCognition(token, boardKey, fields);
        setCognition(next);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setSaving(false);
      }
    },
    [token, boardKey, load],
  );

  const saveWorld = useCallback(
    async (key: WorldKey, value: boolean) => {
      if (!token) return;
      setSaving(true);
      // One switch per write: the gateway merges it into .navin/world-model.json
      // and answers with the whole state. "advise on" comes back 409 while the
      // gate is closed: the switch snaps back and the reason shows.
      const fields: WorldFields = { [key]: value };
      setWorld((prev) => (prev ? { ...prev, ...fields } : prev));
      try {
        const next = await updateWorld(token, boardKey, fields);
        setWorld(next);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setSaving(false);
      }
    },
    [token, boardKey, load],
  );

  const runWorldAction = useCallback(
    async (action: WorldAction, beliefKey?: string) => {
      if (!token) return;
      setBusy(`world:${action}`);
      try {
        const payload = await worldAction(token, boardKey, action, { key: beliefKey });
        setWorld(payload.state);
        setError(null);
        const result = (payload.result ?? {}) as Record<string, unknown>;
        setNotice(
          t(`dev.agi.world.done.${action}`, {
            defaultValue: "{{action}} done",
            action,
            status: typeof result.status === "string" ? result.status : "",
          }),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setBusy(null);
      }
    },
    [token, boardKey, load, t],
  );

  const pressWorld = useCallback(
    (action: WorldAction) => {
      if (CONFIRMED_WORLD_ACTIONS.has(action)) {
        setWorldConfirm(action);
        return;
      }
      void runWorldAction(action);
    },
    [runWorldAction],
  );

  const savePolicy = useCallback(
    async (key: PolicyKey, value: boolean) => {
      if (!token) return;
      setSaving(true);
      // One switch per write: the gateway merges it into .navin/policy.json and
      // answers with the whole state. "enabled on" comes back 409 while the
      // world model radar is not up, "steer on" while the gate is closed: the
      // switch snaps back and the reason shows.
      const fields: PolicyFields = { [key]: value };
      setPolicy((prev) => (prev ? { ...prev, ...fields } : prev));
      try {
        const next = await updatePolicy(token, boardKey, fields);
        setPolicy(next);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setSaving(false);
      }
    },
    [token, boardKey, load],
  );

  const runPolicyAction = useCallback(
    async (action: PolicyAction, options: { name?: string; number?: number } = {}) => {
      if (!token) return;
      setBusy(`policy:${action}`);
      try {
        const payload = await policyAction(token, boardKey, action, options);
        setPolicy(payload.state);
        setError(null);
        const result = (payload.result ?? {}) as Record<string, unknown>;
        setNotice(
          t(`dev.agi.policy.done.${action}`, {
            defaultValue: "{{action}}: {{status}}",
            action,
            status: typeof result.status === "string" ? result.status : "done",
          }),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setBusy(null);
      }
    },
    [token, boardKey, load, t],
  );

  const pressPolicy = useCallback(
    (action: PolicyAction, options: { name?: string; number?: number } = {}) => {
      if (CONFIRMED_POLICY_ACTIONS.has(action)) {
        setPolicyConfirm({ action, ...options });
        return;
      }
      void runPolicyAction(action, options);
    },
    [runPolicyAction],
  );

  const saveTransfer = useCallback(
    async (key: TransferKey, value: boolean) => {
      if (!token) return;
      setSaving(true);
      // One field per write: the gateway merges it into .navin/transfer.json and
      // answers with the whole state. "enabled on" comes back 409 while S2,
      // S3.3 or S4.3 are not up: the switch snaps back and the reason shows.
      const fields: TransferFields = { [key]: value };
      setTransfer((prev) => (prev ? { ...prev, ...fields } : prev));
      try {
        const next = await updateTransfer(token, boardKey, fields);
        setTransfer(next);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setSaving(false);
      }
    },
    [token, boardKey, load],
  );

  const runTransferAction = useCallback(
    async (action: TransferAction, options: { name?: string } = {}) => {
      if (!token) return;
      setBusy(`transfer:${action}`);
      try {
        const payload = await transferAction(token, boardKey, action, options);
        setTransfer(payload.state);
        setError(null);
        const result = (payload.result ?? {}) as Record<string, unknown>;
        setNotice(
          t(`dev.agi.transfer.done.${action}`, {
            defaultValue: "{{action}}: {{status}}",
            action,
            status: typeof result.status === "string" ? result.status : "done",
          }),
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setBusy(null);
      }
    },
    [token, boardKey, load, t],
  );

  const pressTransfer = useCallback(
    (action: TransferAction, options: { name?: string } = {}) => {
      if (CONFIRMED_TRANSFER_ACTIONS.has(action)) {
        setTransferConfirm({ action, ...options });
        return;
      }
      void runTransferAction(action, options);
    },
    [runTransferAction],
  );

  const runAction = useCallback(
    async (action: AgiAction, name?: string, brief?: Record<string, unknown>) => {
      if (!token) return;
      setBusy(name ? `${action}:${name}` : action);
      try {
        const payload = await agiAction(token, boardKey, action, { name, brief });
        setAgi(payload.state);
        setError(null);
        setNotice(
          t(`dev.agi.done.${action}`, {
            defaultValue: "{{action}} done for {{name}}",
            action,
            name: name ?? "",
          }),
        );
        if (action === "discard" && preview?.name === name) setPreview(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        void load({ silent: true });
      } finally {
        setBusy(null);
      }
    },
    [token, boardKey, load, preview, t],
  );

  const press = useCallback(
    (action: AgiAction, name: string) => {
      if (CONFIRMED_ACTIONS.has(action)) {
        setConfirm({ action, name });
        return;
      }
      void runAction(action, name);
    },
    [runAction],
  );

  const openPreview = useCallback(
    async (name: string) => {
      if (!token) return;
      if (preview?.name === name) {
        setPreview(null);
        return;
      }
      try {
        setPreview(await fetchAgiDraft(token, boardKey, name));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [token, boardKey, preview],
  );

  const submitNewDraft = useCallback(() => {
    const name = newName.trim();
    if (!name) return;
    void runAction("draft", name, {
      name,
      description: newDescription.trim() || `Skill ${name} drafted on request`,
      kind: "post_hoc",
    }).then(() => {
      setNewDraftOpen(false);
      setNewName("");
      setNewDescription("");
    });
  }, [newName, newDescription, runAction]);

  const evolutionOn = agi?.enabled ?? false;
  const memoryOn = cognition?.enabled ?? false;
  const worldOn = world?.enabled ?? false;
  const journalSize = journalSizeLabel(cognition?.journal_bytes ?? 0);
  const drafts = agi?.drafts ?? [];
  const pending = agi?.pending_jobs.length ?? 0;
  const battery = agi?.battery;
  const worldButtons = worldActions(world);
  const adviseLocked = adviseSwitchLocked(world);
  const policyOn = policy?.enabled ?? false;
  const policyButtons = policyActions(policy);
  const policyLocked = policySwitchLocked(policy);
  const steerLocked = steerSwitchLocked(policy);
  const forceable = forceableAdapter(policy);
  const policyVerdict: PolicyVerdict | null =
    policy?.score?.verdict_vs_active ?? policy?.score?.verdict_vs_baseline ?? null;

  const policyActionLabel = (action: PolicyAction): string => {
    switch (action) {
      case "train":
        return tx("dev.agi.policy.actions.train", "Run battery and train");
      case "exam":
        return tx("dev.agi.policy.actions.exam", "Re-exam");
      case "ab":
        return tx("dev.agi.policy.actions.ab", "Offline A/B");
      case "freeze":
        return tx("dev.agi.policy.actions.freeze", "Freeze new exam set");
      case "rollback":
        return tx("dev.agi.policy.actions.rollback", "Roll back to N");
      case "force":
        return tx("dev.agi.policy.actions.force", "Force flat adapter");
      case "publish":
        return tx("dev.agi.policy.actions.publish", "Publish adapter");
      case "adopt":
        return tx("dev.agi.policy.actions.adopt", "Adopt");
      case "unpublish":
        return tx("dev.agi.policy.actions.unpublish", "Unpublish");
      default:
        return tx("dev.agi.policy.actions.run", "Run if due");
    }
  };

  const policyActionIcon = (action: PolicyAction): typeof Play => {
    switch (action) {
      case "train":
        return Play;
      case "exam":
        return FlaskConical;
      case "ab":
        return Scale;
      case "freeze":
        return Snowflake;
      case "rollback":
        return RotateCcw;
      case "force":
        return Gavel;
      case "publish":
        return Upload;
      case "adopt":
        return Download;
      case "unpublish":
        return Trash2;
      default:
        return Compass;
    }
  };

  const policyConfirmCopy = (entry: { action: PolicyAction; name?: string; number?: number }): { title: string; description: string } => {
    switch (entry.action) {
      case "freeze":
        return {
          title: tx("dev.agi.policy.confirm.freezeTitle", "Freeze a new exam set?"),
          description: tx(
            "dev.agi.policy.confirm.freeze",
            "The latest held-out episodes become the new frozen set, with a new version. Existing scores stop being comparable; the adapter must prove itself again before steer can speak.",
          ),
        };
      case "rollback":
        return {
          title: tx("dev.agi.policy.confirm.rollbackTitle", "Roll back to the previous adapter?"),
          description: tx(
            "dev.agi.policy.confirm.rollback",
            "The adapter that serves goes back to N-1. The newer one stays on disk as evidence. Steer closes until the gate opens again.",
          ),
        };
      case "force":
        return {
          title: t("dev.agi.policy.confirm.forceTitle", { defaultValue: "Activate adapter {{number}} on purpose?", number: entry.number ?? "" }),
          description: tx(
            "dev.agi.policy.confirm.force",
            "The exam said flat: no regression, no progress. A down adapter can never be forced. Forcing is traced in the journal and reversible with Roll back.",
          ),
        };
      case "publish":
        return {
          title: tx("dev.agi.policy.confirm.publishTitle", "Publish the active adapter outside this project?"),
          description: tx(
            "dev.agi.policy.confirm.publish",
            "The adapter is copied to ~/.navin/policy/published. No other project picks it up by itself: adopting it there is another human click, and it serves there only if it passes that project's exam.",
          ),
        };
      case "adopt":
        return {
          title: t("dev.agi.policy.confirm.adoptTitle", { defaultValue: "Adopt {{name}} into this project?", name: entry.name ?? "" }),
          description: tx(
            "dev.agi.policy.confirm.adopt",
            "The published adapter becomes a new checkpoint here and goes through the same exam as a trained one. It serves only if it beats the reference with no suite down.",
          ),
        };
      default:
        return {
          title: t("dev.agi.policy.confirm.unpublishTitle", { defaultValue: "Remove {{name}} from this machine?", name: entry.name ?? "" }),
          description: tx("dev.agi.policy.confirm.unpublish", "Projects that already adopted it keep their copy."),
        };
    }
  };

  const transferOn = transfer?.enabled ?? false;
  const transferLocked = transferSwitchLocked(transfer);
  const transferButtons = transferActions(transfer);
  const ladder = ladderSteps({ agi, world, policy, transfer });
  const ladderLabel = (id: LadderStepId): string => {
    switch (id) {
      case "skills":
        return tx("dev.agi.evolutionSection", "Skills evolution");
      case "world":
        return tx("dev.agi.world.section", "World model");
      case "policy":
        return tx("dev.agi.policy.section", "Policy");
      case "transfer":
        return tx("dev.agi.transfer.section", "Transfer protocol");
    }
  };

  const transferActionLabel = (action: TransferAction): string => {
    switch (action) {
      case "campaign":
        return tx("dev.agi.transfer.actions.campaign", "Run secret campaign");
      case "replay":
        return tx("dev.agi.transfer.actions.replay", "Replay last campaign");
      case "safety":
        return tx("dev.agi.transfer.actions.safety", "Run safety dossier");
      case "kill_drill":
        return tx("dev.agi.transfer.actions.kill_drill", "Kill drill");
      default:
        return tx("dev.agi.transfer.actions.verify", "Verify suites");
    }
  };

  const transferActionIcon = (action: TransferAction): typeof Play => {
    switch (action) {
      case "campaign":
        return Target;
      case "replay":
        return Repeat;
      case "safety":
        return ShieldAlert;
      case "kill_drill":
        return Power;
      default:
        return Snowflake;
    }
  };

  const transferConfirmCopy = (entry: { action: TransferAction; name?: string }): { title: string; description: string } => {
    switch (entry.action) {
      case "campaign":
        return {
          title: tx("dev.agi.transfer.confirm.campaignTitle", "Run the secret campaign?"),
          description: tx(
            "dev.agi.transfer.confirm.campaign",
            "A child process runs every secret item against the configured model with the sandbox tools: no skill, no recall, no steer, a budget per item. Hours. One family under its bar stops it. The result is a verdict, never a claim.",
          ),
        };
      case "replay":
        return {
          title: t("dev.agi.transfer.confirm.replayTitle", { defaultValue: "Replay campaign {{name}}?", name: entry.name ?? "" }),
          description: tx(
            "dev.agi.transfer.confirm.replay",
            "Same items, same seeds, same suites version. A new record points to the old one so a third party can refuse the number.",
          ),
        };
      default:
        return {
          title: tx("dev.agi.transfer.confirm.killDrillTitle", "Exercise the kill switches?"),
          description: tx(
            "dev.agi.transfer.confirm.killDrill",
            "Token revocation, process stop primitive, network cut and exec switch are exercised on throwaway instances and recorded in the dossier. Nothing is restarted: relaunch is manual.",
          ),
        };
    }
  };

  const worldActionLabel = (action: WorldAction): string => {
    switch (action) {
      case "train":
        return tx("dev.agi.world.actions.train", "Train now");
      case "exam":
        return tx("dev.agi.world.actions.exam", "Re-exam");
      case "ab":
        return tx("dev.agi.world.actions.ab", "Offline A/B");
      case "freeze":
        return tx("dev.agi.world.actions.freeze", "Freeze new exam set");
      case "rollback":
        return tx("dev.agi.world.actions.rollback", "Roll back");
      case "beliefs":
        return tx("dev.agi.world.actions.beliefs", "Write BELIEFS.md");
      case "restore_beliefs":
        return tx("dev.agi.world.actions.restore", "Restore discarded");
      case "discard_belief":
        return tx("dev.agi.world.actions.discard", "Discard");
      default:
        return tx("dev.agi.world.actions.run", "Run if due");
    }
  };

  const worldActionIcon = (action: WorldAction): typeof Play => {
    switch (action) {
      case "train":
        return Play;
      case "exam":
        return FlaskConical;
      case "ab":
        return Scale;
      case "freeze":
        return Snowflake;
      case "rollback":
        return RotateCcw;
      case "beliefs":
        return BookOpen;
      default:
        return Lightbulb;
    }
  };

  const worldConfirmCopy = (action: WorldAction): { title: string; description: string } => {
    if (action === "freeze") {
      return {
        title: tx("dev.agi.world.confirm.freezeTitle", "Freeze a new exam set?"),
        description: tx(
          "dev.agi.world.confirm.freeze",
          "Today's held-out calls become the new frozen set, with a new version. Existing scores stop being comparable; the head must prove itself again before advice can speak.",
        ),
      };
    }
    return {
      title: tx("dev.agi.world.confirm.rollbackTitle", "Roll back to the previous checkpoint?"),
      description: tx(
        "dev.agi.world.confirm.rollback",
        "The head that serves goes back to N-1. The newer checkpoint stays on disk as evidence. Advice closes until the gate opens again.",
      ),
    };
  };

  const actionLabel = (action: AgiAction): string => {
    switch (action) {
      case "promote":
        return tx("dev.agi.actions.promote", "Promote");
      case "force":
        return tx("dev.agi.actions.force", "Force");
      case "publish":
        return tx("dev.agi.actions.publish", "Publish for every project");
      case "rollback":
        return tx("dev.agi.actions.rollback", "Roll back");
      case "discard":
        return tx("dev.agi.actions.discard", "Discard");
      case "exam":
        return tx("dev.agi.actions.exam", "Re-exam");
      case "run":
        return tx("dev.agi.actions.run", "Run pending jobs");
      case "guard":
        return tx("dev.agi.actions.guard", "Guard now");
      default:
        return tx("dev.agi.actions.draft", "New draft");
    }
  };

  const actionIcon = (action: AgiAction): typeof Play => {
    switch (action) {
      case "promote":
        return Award;
      case "force":
        return Gavel;
      case "publish":
        return Upload;
      case "rollback":
        return RotateCcw;
      case "discard":
        return Trash2;
      case "exam":
        return FlaskConical;
      case "guard":
        return ShieldCheck;
      default:
        return Play;
    }
  };

  const confirmCopy = (entry: { action: AgiAction; name: string }): { title: string; description: string } => {
    switch (entry.action) {
      case "publish":
        return {
          title: t("dev.agi.confirm.publishTitle", { defaultValue: "Publish {{name}} for every project?", name: entry.name }),
          description: tx(
            "dev.agi.confirm.publish",
            "The skill is copied to ~/.navin/skills and every project on this machine loads it. Only you can do this; the engine never publishes.",
          ),
        };
      case "force":
        return {
          title: t("dev.agi.confirm.forceTitle", { defaultValue: "Force {{name}} into this project?", name: entry.name }),
          description: tx(
            "dev.agi.confirm.force",
            "The exam said flat: no regression, no progress. Forcing is traced in the journal and reversible with Roll back.",
          ),
        };
      case "rollback":
        return {
          title: t("dev.agi.confirm.rollbackTitle", { defaultValue: "Roll back {{name}}?", name: entry.name }),
          description: tx(
            "dev.agi.confirm.rollback",
            "The skill leaves .navin/skills; its text stays in the draft folder.",
          ),
        };
      default:
        return {
          title: t("dev.agi.confirm.discardTitle", { defaultValue: "Discard the draft {{name}}?", name: entry.name }),
          description: tx("dev.agi.confirm.discard", "The draft folder is removed. The journal keeps the trace."),
        };
    }
  };

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden" data-testid="dev-agi-panel">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border/60 px-3">
        <span
          className={cn(
            "flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-colors",
            evolutionOn || memoryOn || worldOn || policyOn ? ICON_TONE.on : "bg-muted/80 text-muted-foreground",
          )}
          aria-hidden
        >
          <BrainCircuit className="h-3.5 w-3.5" strokeWidth={1.75} />
        </span>
        <span className="text-[13px] font-semibold">{tx("dev.agiTab", "AGI")}</span>
        {agi ? (
          <StatusPill tone={evolutionOn ? "on" : "off"}>
            {evolutionOn
              ? tx("dev.agi.evolutionOn", "Evolution on")
              : tx("dev.agi.evolutionOff", "Evolution off")}
          </StatusPill>
        ) : null}
        {saving || busy ? (
          <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden />
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ml-auto h-7 gap-1.5 px-2 text-[12px]"
          onClick={() => void load()}
          disabled={loading}
          data-testid="dev-agi-refresh"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
          {tx("dev.agi.refresh", "Refresh")}
        </Button>
      </div>

      <AnimatePresence initial={false}>
        {error ? (
          <motion.div
            key="error"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="shrink-0 overflow-hidden border-b border-border/60 bg-destructive/10"
          >
            <div className="flex items-center gap-2 px-3 py-2 text-[12px] text-destructive">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="truncate">{error}</span>
            </div>
          </motion.div>
        ) : notice ? (
          <motion.div
            key="notice"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="shrink-0 overflow-hidden border-b border-border/60 bg-emerald-500/10"
            data-testid="dev-agi-notice"
          >
            <div className="flex items-center gap-2 px-3 py-2 text-[12px] text-emerald-700 dark:text-emerald-300">
              <Sparkles className="h-3.5 w-3.5 shrink-0" aria-hidden />
              <span className="truncate">{notice}</span>
              <button
                type="button"
                className="ml-auto text-[11px] underline-offset-2 hover:underline"
                onClick={() => setNotice(null)}
              >
                {tx("dev.agi.dismiss", "Dismiss")}
              </button>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div
        ref={scrollRef}
        data-panel-scroll=""
        data-testid="dev-agi-scroll"
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
      >
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-4">
          <Section
            index={0}
            title={tx("dev.agi.ladderSection", "How it unlocks")}
            hint={tx(
              "dev.agi.ladderHint",
              "Each stage earns the next one. A grey switch is not broken: it turns clickable by itself once the stage below has passed its exam. Nothing to edit, no file to touch.",
            )}
            testId="dev-agi-ladder"
          >
            <Card>
              <div className="flex flex-wrap items-stretch gap-2 px-3 py-3" data-testid="dev-agi-ladder-steps">
                {ladder.map((step, i) => (
                  <div key={step.id} className="flex min-w-0 items-center gap-2">
                    <div
                      className={cn(
                        "flex min-w-0 items-center gap-2 rounded-xl border px-2.5 py-1.5",
                        step.state === "on"
                          ? "border-emerald-500/30 bg-emerald-500/5"
                          : step.state === "locked"
                            ? "border-amber-500/30 bg-amber-500/5"
                            : "border-border/55 bg-background/40",
                      )}
                      data-testid={`dev-agi-ladder-${step.id}`}
                      data-state={step.state}
                    >
                      {step.state === "locked" ? (
                        <Lock className="h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
                      ) : (
                        <Power
                          className={cn(
                            "h-3.5 w-3.5 shrink-0",
                            step.state === "on" ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground",
                          )}
                          aria-hidden
                        />
                      )}
                      <span className="truncate text-[12px] font-medium">{ladderLabel(step.id)}</span>
                      <StatusPill tone={step.state === "on" ? "on" : step.state === "locked" ? "blocked" : "off"}>
                        {step.state === "on"
                          ? tx("dev.guardrails.on", "On")
                          : step.state === "locked"
                            ? tx("dev.agi.ladderLocked", "Locked")
                            : tx("dev.guardrails.off", "Off")}
                      </StatusPill>
                    </div>
                    {i < ladder.length - 1 ? (
                      <span className="text-[12px] text-muted-foreground/60" aria-hidden>
                        &gt;
                      </span>
                    ) : null}
                  </div>
                ))}
              </div>
              {ladder.some((s) => s.state === "locked") ? (
                <div className="space-y-1 px-3 py-2.5" data-testid="dev-agi-ladder-waits">
                  {ladder
                    .filter((s) => s.state === "locked")
                    .map((step) => (
                      <p key={step.id} className="text-[11.5px] leading-5 text-muted-foreground">
                        <span className="font-medium text-foreground/80">{ladderLabel(step.id)}</span>{" "}
                        {step.id === "policy"
                          ? tx("dev.agi.ladderWaitsPolicy", "waits for the world model to pass its exam.")
                          : tx(
                              "dev.agi.ladderWaitsTransfer",
                              "waits for a skill draft that passed its exam, the world model exam up, and a policy adapter that beat its predecessor.",
                            )}
                        {step.reasons.length ? (
                          <span className="text-muted-foreground/80">
                            {" "}
                            {t("dev.agi.ladderMissing", {
                              defaultValue: "Still missing: {{reasons}}",
                              reasons: step.reasons.join(" / "),
                            })}
                          </span>
                        ) : null}
                      </p>
                    ))}
                </div>
              ) : null}
            </Card>
          </Section>

          <Section
            index={1}
            title={tx("dev.agi.evolutionSection", "Skills evolution")}
            hint={
              agi
                ? tx(
                    "dev.agi.evolutionHint",
                    "Off by default: no draft, no exam, no new skill. On: after the same failure repeats, Navin drafts a skill after the turn, examines it against a frozen battery (code, browser, desk) and only lets it steer this project when the score goes up with no suite down. Publishing it to every project stays your click.",
                  )
                : tx("dev.agi.evolutionUnavailable", "Skills evolution is not available on this gateway.")
            }
            testId="dev-agi-evolution"
          >
            <Card>
              <GuardrailRow
                testId="dev-agi-evolve-enabled"
                emphasis
                checked={evolutionOn}
                onChange={(next) => void saveAgi("enabled", next)}
                disabled={saving || !agi}
                icon={BrainCircuit}
                title={tx("dev.agi.evolve", "Skills evolution")}
                detail={tx(
                  "dev.agi.evolveDetail",
                  "Master switch. Off: nothing is drafted, examined or loaded, and a chat turn costs exactly what it costs today. Stored in .navin/skills-evolve.json.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-evolve-draft"
                checked={agi?.draft ?? false}
                onChange={(next) => void saveAgi("draft", next)}
                disabled={saving || !agi || !evolutionOn}
                icon={PenLine}
                title={tx("dev.agi.draft", "Auto-draft")}
                detail={tx(
                  "dev.agi.draftDetail",
                  "The same tool failure a few times in a row queues a draft job. The draft is written in .navin/skills-draft after the turn, invisible to the agent until it passes.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-evolve-promote"
                checked={agi?.promote_project ?? false}
                onChange={(next) => void saveAgi("promote_project", next)}
                disabled={saving || !agi || !evolutionOn}
                icon={Award}
                title={tx("dev.agi.promote", "Promote to this project")}
                detail={tx(
                  "dev.agi.promoteDetail",
                  "An eligible draft (up overall, no suite down) enters .navin/skills without a click, is verified once more, and rolls back by itself if that check fails. Off: eligible drafts wait for your Promote.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-evolve-publish"
                checked={agi?.publish_harness ?? false}
                onChange={(next) => void saveAgi("publish_harness", next)}
                disabled={saving || !agi || !evolutionOn}
                icon={Upload}
                title={tx("dev.agi.publish", "Allow publishing to every project")}
                detail={tx(
                  "dev.agi.publishDetail",
                  "Shows the Publish button on promoted skills. Publishing copies the skill to ~/.navin/skills and is always your click: the engine cannot do it.",
                )}
              />
            </Card>
            {agi && evolutionOn ? (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11.5px] leading-5 text-muted-foreground" data-testid="dev-agi-battery">
                <span className="inline-flex items-center gap-1.5">
                  <FlaskConical className="h-3 w-3 shrink-0" aria-hidden />
                  {battery?.version
                    ? t("dev.agi.batteryLine", {
                        defaultValue: "Battery {{version}}: {{suites}}",
                        version: battery.version,
                        suites: battery.suites.map((suite) => `${suite.id} ${suite.cases}`).join(", "),
                      })
                    : tx("dev.agi.batteryMissing", "Exam battery unavailable")}
                </span>
                {pending > 0 ? (
                  <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300" data-testid="dev-agi-pending">
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                    {t("dev.agi.pendingJobs", { defaultValue: "{{n}} draft job(s) pending", n: pending })}
                  </span>
                ) : null}
                <span className="ml-auto inline-flex items-center gap-1">
                  {pending > 0 ? (
                    <ActionButton
                      icon={Play}
                      label={actionLabel("run")}
                      onClick={() => void runAction("run")}
                      disabled={busy !== null}
                      testId="dev-agi-run"
                    />
                  ) : null}
                  <ActionButton
                    icon={ShieldCheck}
                    label={actionLabel("guard")}
                    onClick={() => void runAction("guard")}
                    disabled={busy !== null || !drafts.some((draft) => draft.status === "promoted")}
                    testId="dev-agi-guard"
                  />
                  <ActionButton
                    icon={PenLine}
                    label={actionLabel("draft")}
                    onClick={() => setNewDraftOpen((open) => !open)}
                    disabled={busy !== null || !agi.draft}
                    testId="dev-agi-new-draft"
                  />
                </span>
              </div>
            ) : null}
            <AnimatePresence initial={false}>
              {newDraftOpen && agi && evolutionOn ? (
                <motion.form
                  key="new-draft"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.18 }}
                  className="overflow-hidden"
                  onSubmit={(event) => {
                    event.preventDefault();
                    submitNewDraft();
                  }}
                  data-testid="dev-agi-new-draft-form"
                >
                  <div className="flex flex-col gap-2 rounded-2xl border border-border/55 bg-card/40 p-3 sm:flex-row sm:items-center">
                    <Input
                      value={newName}
                      onChange={(event) => setNewName(event.target.value)}
                      placeholder={tx("dev.agi.newDraftName", "skill-name")}
                      className="h-8 text-[12.5px] sm:w-44"
                      aria-label={tx("dev.agi.newDraftName", "skill-name")}
                      data-testid="dev-agi-new-draft-name"
                    />
                    <Input
                      value={newDescription}
                      onChange={(event) => setNewDescription(event.target.value)}
                      placeholder={tx("dev.agi.newDraftDescription", "What should the skill teach?")}
                      className="h-8 flex-1 text-[12.5px]"
                      aria-label={tx("dev.agi.newDraftDescription", "What should the skill teach?")}
                    />
                    <Button
                      type="submit"
                      size="sm"
                      className="h-8 rounded-full px-3 text-[12px]"
                      disabled={!newName.trim() || busy !== null}
                      data-testid="dev-agi-new-draft-submit"
                    >
                      {tx("dev.agi.queueDraft", "Queue draft")}
                    </Button>
                  </div>
                </motion.form>
              ) : null}
            </AnimatePresence>
          </Section>

          <Section
            index={2}
            title={tx("dev.agi.draftsSection", "Drafts and exams")}
            hint={
              evolutionOn
                ? drafts.length
                  ? t("dev.agi.draftsCount", { defaultValue: "{{n}} draft(s)", n: drafts.length })
                  : tx("dev.agi.noDrafts", "No draft yet. Navin writes one after a repeated failure, or queue one above.")
                : tx("dev.agi.draftsOff", "Turn skills evolution on to see drafts and scores.")
            }
            action={
              agi && evolutionOn && agi.journal.length ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 gap-1.5 rounded-full px-2 text-[11.5px]"
                  onClick={() => setJournalOpen((open) => !open)}
                  data-testid="dev-agi-journal-toggle"
                >
                  <History className="h-3 w-3" aria-hidden />
                  {journalOpen ? tx("dev.agi.hideJournal", "Hide journal") : tx("dev.agi.showJournal", "Journal")}
                </Button>
              ) : null
            }
            testId="dev-agi-drafts"
          >
            {evolutionOn && drafts.length ? (
              <div className="space-y-2" data-testid="dev-agi-draft-list">
                <AnimatePresence initial={false}>
                  {drafts.map((draft) => {
                    const verdict = draft.verdict;
                    const actions = draftActions(draft, agi ?? { promote_project: true, publish_harness: false });
                    const isBusy = busy !== null && busy.endsWith(`:${draft.name}`);
                    return (
                      <motion.div
                        key={draft.name}
                        layout={!reduced}
                        initial={reduced ? false : { opacity: 0, y: 6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, height: 0 }}
                        transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
                        className="overflow-hidden rounded-2xl border border-border/55 bg-card/40"
                        data-testid={`dev-agi-draft-${draft.name}`}
                        data-status={draft.status}
                      >
                        <div className="flex items-start gap-3 px-4 py-3">
                          <span
                            className={cn(
                              "mt-px flex h-7 w-7 shrink-0 items-center justify-center rounded-lg",
                              draft.status === "promoted" || draft.status === "eligible"
                                ? ICON_TONE.on
                                : draft.status === "rejected"
                                  ? "bg-red-500/12 text-red-600 dark:text-red-400"
                                  : ICON_TONE.off,
                            )}
                            aria-hidden
                          >
                            <FlaskConical className="h-3.5 w-3.5" strokeWidth={1.75} />
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="truncate text-[13px] font-medium leading-5 text-foreground">{draft.name}</span>
                              <span
                                className={cn(
                                  "shrink-0 rounded-full px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide",
                                  STATUS_PILL[draft.status] ?? PILL_TONE.off,
                                )}
                                data-testid={`dev-agi-draft-${draft.name}-status`}
                              >
                                {tx(`dev.agi.status.${draft.status}`, draft.status)}
                              </span>
                              {draft.in_harness ? (
                                <StatusPill tone="on">{tx("dev.agi.published", "Published")}</StatusPill>
                              ) : null}
                              {draft.forced_by ? (
                                <StatusPill tone="blocked">{tx("dev.agi.forced", "Forced")}</StatusPill>
                              ) : null}
                            </div>
                            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] leading-5 text-muted-foreground">
                              <span className="inline-flex items-center gap-1 tabular-nums" data-testid={`dev-agi-draft-${draft.name}-score`}>
                                <VerdictIcon
                                  verdict={verdict}
                                  className={cn("h-3.5 w-3.5", VERDICT_TONE[verdict ?? "flat"])}
                                />
                                <span className={cn("font-semibold", VERDICT_TONE[verdict ?? "flat"])}>
                                  {scoreLabel(draft.score)}
                                </span>
                                {draft.baseline_score !== null ? (
                                  <span>
                                    {t("dev.agi.fromBaseline", {
                                      defaultValue: "from {{score}}",
                                      score: scoreLabel(draft.baseline_score),
                                    })}
                                  </span>
                                ) : null}
                                {verdict ? (
                                  <span className={VERDICT_TONE[verdict]}>{tx(`dev.agi.verdict.${verdict}`, verdict)}</span>
                                ) : null}
                              </span>
                              {Object.keys(draft.suites).length ? (
                                <span className="inline-flex flex-wrap items-center gap-1">
                                  {Object.entries(draft.suites).map(([suite, value]) => (
                                    <span
                                      key={suite}
                                      className={cn(
                                        "inline-flex items-center gap-0.5 rounded-full bg-muted/70 px-1.5 text-[10.5px]",
                                        VERDICT_TONE[value],
                                      )}
                                    >
                                      {suite}
                                      <VerdictIcon verdict={value} className="h-3 w-3" />
                                    </span>
                                  ))}
                                </span>
                              ) : null}
                              <span>
                                {t("dev.agi.attempts", { defaultValue: "{{n}} attempt(s)", n: draft.attempts })}
                              </span>
                            </div>
                            {draft.trigger ? (
                              <p className="mt-0.5 truncate text-[11.5px] leading-5 text-muted-foreground/80">
                                {draft.trigger}
                              </p>
                            ) : null}
                            {draft.note ? (
                              <p className="mt-0.5 text-[11.5px] leading-5 text-amber-700 dark:text-amber-300">
                                {draft.note}
                              </p>
                            ) : null}
                            <div className="mt-2 flex flex-wrap items-center gap-1">
                              <ActionButton
                                icon={Eye}
                                label={preview?.name === draft.name ? tx("dev.agi.hidePreview", "Hide") : tx("dev.agi.preview", "View")}
                                onClick={() => void openPreview(draft.name)}
                                testId={`dev-agi-draft-${draft.name}-preview`}
                              />
                              {actions.map((action) => (
                                <ActionButton
                                  key={action}
                                  icon={actionIcon(action)}
                                  label={actionLabel(action)}
                                  onClick={() => press(action, draft.name)}
                                  disabled={busy !== null}
                                  tone={
                                    action === "publish" || action === "promote"
                                      ? "primary"
                                      : action === "discard" || action === "rollback"
                                        ? "danger"
                                        : "ghost"
                                  }
                                  testId={`dev-agi-draft-${draft.name}-${action}`}
                                />
                              ))}
                              {isBusy ? <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden /> : null}
                            </div>
                          </div>
                        </div>
                        <AnimatePresence initial={false}>
                          {preview?.name === draft.name ? (
                            <motion.pre
                              key="preview"
                              initial={{ opacity: 0, height: 0 }}
                              animate={{ opacity: 1, height: "auto" }}
                              exit={{ opacity: 0, height: 0 }}
                              transition={{ duration: 0.18 }}
                              className="max-h-72 overflow-auto border-t border-border/40 bg-muted/30 px-4 py-3 text-[11.5px] leading-5 whitespace-pre-wrap"
                              data-testid={`dev-agi-draft-${draft.name}-markdown`}
                            >
                              {preview.markdown}
                            </motion.pre>
                          ) : null}
                        </AnimatePresence>
                      </motion.div>
                    );
                  })}
                </AnimatePresence>
              </div>
            ) : null}
            <AnimatePresence initial={false}>
              {journalOpen && agi && evolutionOn ? (
                <motion.ul
                  key="journal"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.18 }}
                  className="overflow-hidden rounded-2xl border border-border/55 bg-card/40 divide-y divide-border/40"
                  data-testid="dev-agi-journal"
                >
                  {[...agi.journal].reverse().slice(0, 12).map((entry, index) => (
                    <li key={`${entry.ts}-${index}`} className="flex flex-wrap items-baseline gap-x-2 px-4 py-1.5 text-[11.5px] leading-5">
                      <span className="tabular-nums text-muted-foreground/70">{String(entry.ts).replace("T", " ").replace("Z", "")}</span>
                      <span className="font-semibold">{String(entry.event)}</span>
                      <span className="text-muted-foreground">{entry.name ? String(entry.name) : ""}</span>
                      {"score" in entry && typeof entry.score === "number" ? (
                        <span className="tabular-nums text-muted-foreground">{scoreLabel(entry.score)}</span>
                      ) : null}
                      {"verdict" in entry && typeof entry.verdict === "string" ? (
                        <span className={VERDICT_TONE[(entry.verdict as "up" | "flat" | "down") ?? "flat"] ?? ""}>{String(entry.verdict)}</span>
                      ) : null}
                      {"reason" in entry && entry.reason ? (
                        <span className="truncate text-muted-foreground/80">{String(entry.reason)}</span>
                      ) : null}
                    </li>
                  ))}
                </motion.ul>
              ) : null}
            </AnimatePresence>
          </Section>

          <Section
            index={3}
            title={tx("dev.agi.world.section", "World model")}
            hint={
              world
                ? tx(
                    "dev.agi.world.hint",
                    "Off by default: no tool log, no training, no advice. On: every tool call leaves one secret-free line after the call and a local head trains outside the chat, judged by its prediction error on a frozen set. Live advice only opens once that error went down and an A/B proved a gain, and it cuts itself off if it regresses.",
                  )
                : tx("dev.agi.world.unavailable", "The world model is not available on this gateway.")
            }
            action={
              world && worldOn && world.journal.length ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 gap-1.5 rounded-full px-2 text-[11.5px]"
                  onClick={() => setWorldJournalOpen((open) => !open)}
                  data-testid="dev-agi-world-journal-toggle"
                >
                  <History className="h-3 w-3" aria-hidden />
                  {worldJournalOpen ? tx("dev.agi.hideJournal", "Hide journal") : tx("dev.agi.showJournal", "Journal")}
                </Button>
              ) : null
            }
            testId="dev-agi-world"
          >
            <Card>
              <GuardrailRow
                testId="dev-agi-world-enabled"
                emphasis
                checked={worldOn}
                onChange={(next) => void saveWorld("enabled", next)}
                disabled={saving || !world}
                icon={Globe}
                title={tx("dev.agi.world.enabled", "World model")}
                detail={tx(
                  "dev.agi.world.enabledDetail",
                  "Master switch. Off: no journal line, no training job, no advice, and a chat turn costs exactly what it costs today. Stored in .navin/world-model.json.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-world-log"
                checked={world?.log ?? false}
                onChange={(next) => void saveWorld("log", next)}
                disabled={saving || !world || !worldOn}
                icon={ScrollText}
                title={tx("dev.agi.world.log", "Tool journal")}
                detail={tx(
                  "dev.agi.world.logDetail",
                  "One line per tool call in .navin/world/trajectories.jsonl: tool, hashed arguments, observation class (ok, not_found, denied, timeout, changed...), duration. No raw output, no secret. Written by a background thread after the call.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-world-train"
                checked={world?.train ?? false}
                onChange={(next) => void saveWorld("train", next)}
                disabled={saving || !world || !worldOn}
                icon={Activity}
                title={tx("dev.agi.world.train", "Train offline")}
                detail={tx(
                  "dev.agi.world.trainDetail",
                  "After enough new calls, a job outside the chat fits a small local head (not the chat LLM), scores it on the frozen exam set against the baselines, and activates the checkpoint only if it learned. Down: the checkpoint is kept as evidence, nothing serves it. Rollback is one click.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-world-advise"
                checked={world?.advise ?? false}
                onChange={(next) => void saveWorld("advise", next)}
                disabled={saving || !world || adviseLocked}
                icon={world && worldOn && adviseLocked ? Lock : Lightbulb}
                title={tx("dev.agi.world.advise", "Live advice")}
                detail={
                  world && worldOn && adviseLocked
                    ? t("dev.agi.world.adviseLocked", {
                        defaultValue: "Gate closed: {{reasons}}. The switch opens once the exam says up and the offline A/B says gain.",
                        reasons: world.gate.reasons.join("; "),
                      })
                    : tx(
                        "dev.agi.world.adviseDetail",
                        "Three lines at most before the agent picks its tools, plus the world_predict tool. Advisory only: it never replaces the real call and never skips a write, delete, mail or payment. Cuts itself off if the live precision drops.",
                      )
                }
              />
              <GuardrailRow
                testId="dev-agi-world-beliefs"
                checked={world?.beliefs ?? false}
                onChange={(next) => void saveWorld("beliefs", next)}
                disabled={saving || !world || !worldOn}
                icon={BookOpen}
                title={tx("dev.agi.world.beliefs", "Readable beliefs")}
                detail={tx(
                  "dev.agi.world.beliefsDetail",
                  "Refresh .navin/BELIEFS.md after each checkpoint: ten lines at most, like 'npm test -> error (87%)'. A summary for you, never read by the prompt while advice is off. Edit or discard lines freely.",
                )}
              />
            </Card>
            {world && worldOn ? (
              <div className="space-y-2" data-testid="dev-agi-world-stats">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11.5px] leading-5 text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-world-rows">
                    <ScrollText className="h-3 w-3 shrink-0" aria-hidden />
                    {t("dev.agi.world.rows", { defaultValue: "{{n}} call(s) logged", n: world.rows })}
                  </span>
                  <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-world-heldout">
                    <Snowflake className="h-3 w-3 shrink-0" aria-hidden />
                    {world.heldout.version
                      ? t("dev.agi.world.heldout", {
                          defaultValue: "Exam set {{version}}: {{n}} calls",
                          version: world.heldout.version,
                          n: world.heldout.rows,
                        })
                      : tx("dev.agi.world.noHeldout", "No exam set frozen yet")}
                  </span>
                  <span className="inline-flex items-center gap-1.5 tabular-nums" data-testid="dev-agi-world-score">
                    <VerdictIcon
                      verdict={world.score?.verdict ?? null}
                      className={cn("h-3.5 w-3.5", VERDICT_TONE[world.score?.verdict ?? "flat"])}
                    />
                    {world.score
                      ? t("dev.agi.world.score", {
                          defaultValue: "Checkpoint {{checkpoint}}: log-loss {{score}}, wrong class {{errors}}",
                          checkpoint: world.score.checkpoint,
                          score: worldScoreLabel(world.score),
                          errors: `${percentLabel(world.score.baseline_error_rate)} -> ${percentLabel(world.score.error_rate)}`,
                        })
                      : tx("dev.agi.world.noScore", "No checkpoint scored yet")}
                    {world.score?.verdict ? (
                      <span className={cn("font-semibold", VERDICT_TONE[world.score.verdict])}>
                        {tx(`dev.agi.verdict.${world.score.verdict}`, world.score.verdict)}
                      </span>
                    ) : null}
                  </span>
                  <span
                    className={cn("inline-flex items-center gap-1.5", world.gate.open ? VERDICT_TONE.up : "")}
                    data-testid="dev-agi-world-gate"
                    data-open={world.gate.open ? "true" : "false"}
                  >
                    {world.gate.open ? <ShieldCheck className="h-3 w-3" aria-hidden /> : <Lock className="h-3 w-3" aria-hidden />}
                    {world.gate.open
                      ? tx("dev.agi.world.gateOpen", "Advice gate open")
                      : tx("dev.agi.world.gateClosed", "Advice gate closed")}
                    {!world.gate.open && world.gate.reasons.length > 0 ? (
                      <span className="text-muted-foreground" data-testid="dev-agi-world-gate-reasons">
                        {world.gate.reasons.join(" / ")}
                      </span>
                    ) : null}
                    {world.ab ? (
                      <span className={VERDICT_TONE[world.ab.verdict === "gain" ? "up" : world.ab.verdict === "regress" ? "down" : "flat"]}>
                        {t("dev.agi.world.abLine", {
                          defaultValue: "A/B {{verdict}}: {{avoided}} useless call(s) avoided, {{alarms}} false alarm(s)",
                          verdict: world.ab.verdict,
                          avoided: world.ab.avoided,
                          alarms: world.ab.false_alarms,
                        })}
                      </span>
                    ) : null}
                  </span>
                  {world.advise && world.tool_registered === true ? (
                    <span className={cn("inline-flex items-center gap-1.5", VERDICT_TONE.up)} data-testid="dev-agi-world-tool-live">
                      <Lightbulb className="h-3 w-3" aria-hidden />
                      {tx("dev.agi.world.toolLive", "world_predict tool live in the agent")}
                    </span>
                  ) : null}
                  {world.live.advised > 0 ? (
                    <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-world-live">
                      <Activity className="h-3 w-3" aria-hidden />
                      {t("dev.agi.world.live", {
                        defaultValue: "Live: {{n}} advice(s), precision {{precision}}",
                        n: world.live.advised,
                        precision: percentLabel(world.live.precision),
                      })}
                    </span>
                  ) : null}
                </div>
                {worldButtons.length ? (
                  <div className="flex flex-wrap items-center gap-1 px-1" data-testid="dev-agi-world-actions">
                    {worldButtons.map((action) => (
                      <ActionButton
                        key={action}
                        icon={worldActionIcon(action)}
                        label={worldActionLabel(action)}
                        onClick={() => pressWorld(action)}
                        disabled={busy !== null}
                        tone={action === "train" ? "primary" : action === "rollback" ? "danger" : "ghost"}
                        testId={`dev-agi-world-action-${action}`}
                      />
                    ))}
                    {busy?.startsWith("world:") ? <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden /> : null}
                  </div>
                ) : (
                  <p className="px-1 text-[11.5px] leading-5 text-muted-foreground" data-testid="dev-agi-world-waiting">
                    {t("dev.agi.world.waiting", {
                      defaultValue: "Training opens at {{n}} logged calls. Keep working; the journal fills itself.",
                      n: world.min_rows,
                    })}
                  </p>
                )}
                {world.belief_items.length || world.beliefs_ignored.length ? (
                  <div className="rounded-2xl border border-border/55 bg-card/40" data-testid="dev-agi-world-beliefs-list">
                    <div className="flex items-center gap-2 px-4 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      <BookOpen className="h-3 w-3" aria-hidden />
                      {tx("dev.agi.world.beliefsTitle", "Beliefs of the active head")}
                      {world.beliefs_ignored.length ? (
                        <button
                          type="button"
                          className="ml-auto text-[11px] font-normal normal-case tracking-normal underline-offset-2 hover:underline"
                          onClick={() => void runWorldAction("restore_beliefs")}
                          disabled={busy !== null}
                          data-testid="dev-agi-world-action-restore_beliefs"
                        >
                          {t("dev.agi.world.restoreCount", {
                            defaultValue: "Restore {{n}} discarded",
                            n: world.beliefs_ignored.length,
                          })}
                        </button>
                      ) : null}
                    </div>
                    <ul className="divide-y divide-border/40 pb-1">
                      {world.belief_items.map((belief) => (
                        <li key={belief.key} className="flex items-center gap-2 px-4 py-1.5 text-[12px] leading-5" data-testid={`dev-agi-world-belief-${belief.key}`}>
                          <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", belief.cls === "error" || belief.cls === "denied" ? "bg-red-500" : "bg-amber-500")} aria-hidden />
                          <span className="min-w-0 flex-1 truncate">{belief.text}</span>
                          <button
                            type="button"
                            className="shrink-0 rounded-full p-1 text-muted-foreground hover:text-destructive"
                            onClick={() => void runWorldAction("discard_belief", belief.key)}
                            disabled={busy !== null}
                            aria-label={worldActionLabel("discard_belief")}
                            data-testid={`dev-agi-world-belief-${belief.key}-discard`}
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden />
                          </button>
                        </li>
                      ))}
                      {!world.belief_items.length ? (
                        <li className="px-4 py-1.5 text-[12px] leading-5 text-muted-foreground">
                          {tx("dev.agi.world.noBeliefs", "No confident belief yet.")}
                        </li>
                      ) : null}
                    </ul>
                  </div>
                ) : null}
                {world.recent.length ? (
                  <ul className="flex flex-wrap gap-1 px-1" data-testid="dev-agi-world-recent">
                    {world.recent.slice(0, 8).map((row, index) => (
                      <li
                        key={`${row.ts}-${index}`}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-full bg-muted/70 px-2 py-px text-[10.5px]",
                          row.ok ? "text-muted-foreground" : VERDICT_TONE.down,
                        )}
                        title={row.obs}
                      >
                        <span className="max-w-[12rem] truncate">{row.key.replace(/\|/g, " ").trim()}</span>
                        <span className="font-semibold">{row.cls}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <AnimatePresence initial={false}>
                  {worldJournalOpen ? (
                    <motion.ul
                      key="world-journal"
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.18 }}
                      className="overflow-hidden rounded-2xl border border-border/55 bg-card/40 divide-y divide-border/40"
                      data-testid="dev-agi-world-journal"
                    >
                      {[...world.journal].reverse().slice(0, 12).map((entry, index) => (
                        <li key={`${entry.ts}-${index}`} className="flex flex-wrap items-baseline gap-x-2 px-4 py-1.5 text-[11.5px] leading-5">
                          <span className="tabular-nums text-muted-foreground/70">{String(entry.ts).replace("T", " ").replace("Z", "")}</span>
                          <span className="font-semibold">{String(entry.event)}</span>
                          {"checkpoint" in entry && typeof entry.checkpoint === "number" ? (
                            <span className="text-muted-foreground">#{entry.checkpoint}</span>
                          ) : null}
                          {"verdict" in entry && typeof entry.verdict === "string" ? (
                            <span className={VERDICT_TONE[(entry.verdict as "up" | "flat" | "down") ?? "flat"] ?? ""}>{String(entry.verdict)}</span>
                          ) : null}
                          {"reason" in entry && entry.reason ? (
                            <span className="truncate text-muted-foreground/80">{String(entry.reason)}</span>
                          ) : null}
                        </li>
                      ))}
                    </motion.ul>
                  ) : null}
                </AnimatePresence>
              </div>
            ) : null}
          </Section>

          <Section
            index={4}
            title={tx("dev.agi.policy.section", "Policy")}
            hint={
              policy
                ? tx(
                    "dev.agi.policy.hint",
                    "Off by default: no trajectory, no training, no steer. On: a job outside the chat runs frozen batteries (code, browser, desk) in sandboxes, logs each step with its eval reward, and trains a small local adapter, never the chat model. Adapter N+1 serves only if it beats N on a frozen split with no suite down; otherwise N stays. Live steer opens only after that and an A/B, proposes the next tool, executes nothing, and cuts itself off if it regresses. Needs the world model radar up.",
                  )
                : tx("dev.agi.policy.unavailable", "Policy learning is not available on this gateway.")
            }
            action={
              policy && policyOn && policy.journal.length ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 gap-1.5 rounded-full px-2 text-[11.5px]"
                  onClick={() => setPolicyJournalOpen((open) => !open)}
                  data-testid="dev-agi-policy-journal-toggle"
                >
                  <History className="h-3 w-3" aria-hidden />
                  {policyJournalOpen ? tx("dev.agi.hideJournal", "Hide journal") : tx("dev.agi.showJournal", "Journal")}
                </Button>
              ) : null
            }
            testId="dev-agi-policy"
          >
            <Card>
              <GuardrailRow
                testId="dev-agi-policy-enabled"
                emphasis
                checked={policyOn}
                onChange={(next) => void savePolicy("enabled", next)}
                disabled={saving || !policy || policyLocked}
                icon={policy && policyLocked ? Lock : Compass}
                title={tx("dev.agi.policy.enabled", "Policy learning")}
                detail={
                  policy && policyLocked
                    ? t("dev.agi.policy.enabledLocked", {
                        defaultValue: "No policy without a radar: {{reasons}}. Turn the world model on and let it pass its exam first.",
                        reasons: policy.radar.reasons.join("; "),
                      })
                    : tx(
                        "dev.agi.policy.enabledDetail",
                        "Master switch. Off: no trajectory, no training job, no steer, and a chat turn costs exactly what it costs today. Stored in .navin/policy.json. The chat model is never fine-tuned; the adapter is a small local head beside it.",
                      )
                }
              />
              <GuardrailRow
                testId="dev-agi-policy-log"
                checked={policy?.log ?? false}
                onChange={(next) => void savePolicy("log", next)}
                disabled={saving || !policy || !policyOn}
                icon={ScrollText}
                title={tx("dev.agi.policy.log", "Eval trajectories")}
                detail={tx(
                  "dev.agi.policy.logDetail",
                  "One line per step of an eval episode in .navin/policy/trajectories.jsonl: intent, last calls, world model class, action, observation class, eval reward (pass or fail of the whole episode). Written by the training process after the run, never from a chat turn, never from a thank-you.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-policy-train"
                checked={policy?.train ?? false}
                onChange={(next) => void savePolicy("train", next)}
                disabled={saving || !policy || !policyOn}
                icon={Activity}
                title={tx("dev.agi.policy.train", "Train adapter offline")}
                detail={tx(
                  "dev.agi.policy.trainDetail",
                  "Every train_every turns, a child process (not the gateway) runs the battery in sandboxes, fits adapter N+1 under a time and memory budget, scores it on the frozen split against the baseline and against N, per suite. Up with no suite down: N+1 serves. Flat: N stays. Down: N stays, N+1 kept as evidence. Crash or budget: nothing moves.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-policy-steer"
                checked={policy?.steer ?? false}
                onChange={(next) => void savePolicy("steer", next)}
                disabled={saving || !policy || steerLocked}
                icon={policy && policyOn && steerLocked ? Lock : Lightbulb}
                title={tx("dev.agi.policy.steer", "Live steer")}
                detail={
                  policy && policyOn && steerLocked
                    ? t("dev.agi.policy.steerLocked", {
                        defaultValue: "Gate closed: {{reasons}}. The switch opens once N+1 beat N with no suite down and the offline A/B says gain.",
                        reasons: policy.gate.reasons.join("; "),
                      })
                    : tx(
                        "dev.agi.policy.steerDetail",
                        "One advisory line before the agent picks its first tool, plus the policy_next tool: what successful runs did next, what only failed runs did. Soft only: the chat LLM stays the generator, nothing is executed or blocked, writes, mail, payment and delete keep their approval. Cuts itself off and reloads N if the live precision drops.",
                      )
                }
              />
            </Card>
            {policy ? (
              <div className="space-y-2" data-testid="dev-agi-policy-stats">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11.5px] leading-5 text-muted-foreground">
                  <span
                    className={cn("inline-flex items-center gap-1.5", policy.radar.up ? VERDICT_TONE.up : "")}
                    data-testid="dev-agi-policy-radar"
                    data-up={policy.radar.up ? "true" : "false"}
                  >
                    <Radar className="h-3 w-3 shrink-0" aria-hidden />
                    {policy.radar.up
                      ? t("dev.agi.policy.radarUp", {
                          defaultValue: "Radar up (world model checkpoint {{checkpoint}})",
                          checkpoint: policy.radar.checkpoint ?? "-",
                        })
                      : tx("dev.agi.policy.radarDown", "Radar not up: the world model has not passed its exam")}
                  </span>
                  {policyOn ? (
                    <>
                      <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-policy-rows">
                        <ScrollText className="h-3 w-3 shrink-0" aria-hidden />
                        {t("dev.agi.policy.rows", {
                          defaultValue: "{{n}} step(s) from {{episodes}} eval episode(s)",
                          n: policy.rows,
                          episodes: policy.episodes,
                        })}
                      </span>
                      <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-policy-battery">
                        <FlaskConical className="h-3 w-3 shrink-0" aria-hidden />
                        {policy.battery.version
                          ? t("dev.agi.policy.battery", {
                              defaultValue: "Battery {{version}}: {{n}} cases ({{suites}})",
                              version: policy.battery.version,
                              n: policy.battery.total_cases ?? 0,
                              suites: policy.battery.suites.map((suite) => `${suite.id} ${suite.cases}`).join(", "),
                            })
                          : policy.battery.error ?? tx("dev.agi.policy.noBattery", "No battery")}
                      </span>
                      <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-policy-heldout">
                        <Snowflake className="h-3 w-3 shrink-0" aria-hidden />
                        {policy.heldout.version
                          ? t("dev.agi.policy.heldout", {
                              defaultValue: "Exam set {{version}}: {{n}} steps",
                              version: policy.heldout.version,
                              n: policy.heldout.rows,
                            })
                          : tx("dev.agi.policy.noHeldout", "No exam set frozen yet")}
                      </span>
                      <span className="inline-flex items-center gap-1.5 tabular-nums" data-testid="dev-agi-policy-score">
                        <VerdictIcon
                          verdict={policyVerdict?.overall ?? null}
                          className={cn("h-3.5 w-3.5", VERDICT_TONE[policyVerdict?.overall ?? "flat"])}
                        />
                        {policy.score
                          ? t("dev.agi.policy.score", {
                              defaultValue: "Adapter {{checkpoint}}: next-action accuracy {{score}} vs {{reference}}",
                              checkpoint: policy.score.checkpoint,
                              score: policyScoreLabel(policy.score),
                              reference: policy.score.reference === "active" ? "N" : tx("dev.agi.policy.baseline", "baseline"),
                            })
                          : tx("dev.agi.policy.noScore", "No adapter scored yet")}
                        {policyVerdict ? (
                          <span className={cn("font-semibold", VERDICT_TONE[policyVerdict.overall])}>
                            {tx(`dev.agi.verdict.${policyVerdict.overall}`, policyVerdict.overall)}
                          </span>
                        ) : null}
                        {policyVerdict && Object.keys(policyVerdict.suites).length ? (
                          <span className="text-muted-foreground" data-testid="dev-agi-policy-suites">
                            {suiteVerdictsLabel(policyVerdict)}
                          </span>
                        ) : null}
                      </span>
                      <span
                        className={cn("inline-flex items-center gap-1.5", policy.gate.open ? VERDICT_TONE.up : "")}
                        data-testid="dev-agi-policy-gate"
                        data-open={policy.gate.open ? "true" : "false"}
                      >
                        {policy.gate.open ? <ShieldCheck className="h-3 w-3" aria-hidden /> : <Lock className="h-3 w-3" aria-hidden />}
                        {policy.gate.open
                          ? tx("dev.agi.policy.gateOpen", "Steer gate open")
                          : tx("dev.agi.policy.gateClosed", "Steer gate closed")}
                        {!policy.gate.open && policy.gate.reasons.length > 0 ? (
                          <span className="text-muted-foreground" data-testid="dev-agi-policy-gate-reasons">
                            {policy.gate.reasons.join(" / ")}
                          </span>
                        ) : null}
                        {policy.ab ? (
                          <span className={VERDICT_TONE[policy.ab.verdict === "gain" ? "up" : policy.ab.verdict === "regress" ? "down" : "flat"]}>
                            {t("dev.agi.policy.abLine", {
                              defaultValue: "A/B {{verdict}}: precision {{precision}} on {{coverage}} of the steps, no-steer {{baseline}}",
                              verdict: policy.ab.verdict,
                              precision: percentLabel(policy.ab.precision),
                              coverage: percentLabel(policy.ab.coverage),
                              baseline: percentLabel(policy.ab.baseline_accuracy),
                            })}
                          </span>
                        ) : null}
                      </span>
                      {policy.steer && policy.tool_registered === true ? (
                        <span className={cn("inline-flex items-center gap-1.5", VERDICT_TONE.up)} data-testid="dev-agi-policy-tool-live">
                          <Lightbulb className="h-3 w-3" aria-hidden />
                          {tx("dev.agi.policy.toolLive", "policy_next tool live in the agent")}
                        </span>
                      ) : null}
                      {policy.live.suggested > 0 ? (
                        <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-policy-live">
                          <Activity className="h-3 w-3" aria-hidden />
                          {t("dev.agi.policy.live", {
                            defaultValue: "Live: {{n}} suggestion(s), precision {{precision}}",
                            n: policy.live.suggested,
                            precision: percentLabel(policy.live.precision),
                          })}
                        </span>
                      ) : null}
                      {policy.train ? (
                        <span className="inline-flex items-center gap-1.5" data-testid="dev-agi-policy-turns">
                          <RefreshCw className="h-3 w-3" aria-hidden />
                          {t("dev.agi.policy.turns", {
                            defaultValue: "Next automatic training in {{n}} turn(s)",
                            n: Math.max(0, policy.train_every - policy.turns_since_train),
                          })}
                        </span>
                      ) : null}
                    </>
                  ) : null}
                </div>
                {policyOn ? (
                  policyButtons.length ? (
                    <div className="flex flex-wrap items-center gap-1 px-1" data-testid="dev-agi-policy-actions">
                      {policyButtons.map((action) => (
                        <ActionButton
                          key={action}
                          icon={policyActionIcon(action)}
                          label={policyActionLabel(action)}
                          onClick={() => pressPolicy(action)}
                          disabled={busy !== null}
                          tone={action === "train" ? "primary" : action === "rollback" ? "danger" : "ghost"}
                          testId={`dev-agi-policy-action-${action}`}
                        />
                      ))}
                      {forceable !== null ? (
                        <ActionButton
                          icon={Gavel}
                          label={t("dev.agi.policy.forceNumber", { defaultValue: "Force adapter {{n}}", n: forceable })}
                          onClick={() => pressPolicy("force", { number: forceable })}
                          disabled={busy !== null}
                          tone="ghost"
                          testId="dev-agi-policy-action-force"
                        />
                      ) : null}
                      {busy?.startsWith("policy:") ? <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden /> : null}
                    </div>
                  ) : (
                    <p className="px-1 text-[11.5px] leading-5 text-muted-foreground" data-testid="dev-agi-policy-waiting">
                      {tx("dev.agi.policy.waiting", "Training opens once the world model radar is up.")}
                    </p>
                  )
                ) : null}
                {policyOn && policy.checkpoints.length ? (
                  <div className="rounded-2xl border border-border/55 bg-card/40" data-testid="dev-agi-policy-adapters">
                    <div className="flex items-center gap-2 px-4 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      <Compass className="h-3 w-3" aria-hidden />
                      {tx("dev.agi.policy.adaptersTitle", "Adapters N / N+1")}
                    </div>
                    <ul className="divide-y divide-border/40 pb-1">
                      {[...policy.checkpoints].reverse().slice(0, 6).map((item) => {
                        const verdict = item.verdict_vs_active ?? item.verdict_vs_baseline;
                        return (
                          <li
                            key={item.number}
                            className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-4 py-1.5 text-[12px] leading-5 tabular-nums"
                            data-testid={`dev-agi-policy-adapter-${item.number}`}
                          >
                            <span className="font-semibold">#{item.number}</span>
                            <span className="text-muted-foreground">
                              {percentLabel(item.metrics?.accuracy)} ({item.metrics?.score ?? "-"}/20)
                            </span>
                            {verdict ? (
                              <span className={cn("font-semibold", VERDICT_TONE[verdict.overall])}>
                                {tx(`dev.agi.verdict.${verdict.overall}`, verdict.overall)}
                                {item.verdict_vs_active ? " vs N" : ""}
                              </span>
                            ) : null}
                            {verdict && Object.keys(verdict.suites).length ? (
                              <span className="text-muted-foreground/80">{suiteVerdictsLabel(verdict)}</span>
                            ) : null}
                            {item.source !== "trained" ? (
                              <span className="rounded-full bg-muted/70 px-1.5 text-[10.5px] text-muted-foreground">{item.source}</span>
                            ) : null}
                            <span className="ml-auto text-[11px]">
                              {item.active ? (
                                <span className={VERDICT_TONE.up}>
                                  {item.forced ? tx("dev.agi.policy.servingForced", "serving (forced)") : tx("dev.agi.policy.serving", "serving")}
                                </span>
                              ) : item.previous ? (
                                <span className="text-muted-foreground">{tx("dev.agi.policy.previous", "previous")}</span>
                              ) : null}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null}
                {policyOn && policy.published.length ? (
                  <div className="rounded-2xl border border-border/55 bg-card/40" data-testid="dev-agi-policy-published">
                    <div className="flex items-center gap-2 px-4 pt-2.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                      <Upload className="h-3 w-3" aria-hidden />
                      {tx("dev.agi.policy.publishedTitle", "Published on this machine")}
                    </div>
                    <ul className="divide-y divide-border/40 pb-1">
                      {policy.published.slice(0, 6).map((item) => (
                        <li
                          key={item.name}
                          className="flex items-center gap-2 px-4 py-1.5 text-[12px] leading-5"
                          data-testid={`dev-agi-policy-published-${item.name}`}
                        >
                          <span className="min-w-0 flex-1 truncate" title={item.from}>
                            {item.name}
                            <span className="ml-1 text-muted-foreground tabular-nums">{percentLabel(item.metrics?.accuracy)}</span>
                          </span>
                          <button
                            type="button"
                            className="shrink-0 rounded-full p-1 text-muted-foreground hover:text-foreground"
                            onClick={() => pressPolicy("adopt", { name: item.name })}
                            disabled={busy !== null}
                            aria-label={policyActionLabel("adopt")}
                            data-testid={`dev-agi-policy-published-${item.name}-adopt`}
                          >
                            <Download className="h-3.5 w-3.5" aria-hidden />
                          </button>
                          <button
                            type="button"
                            className="shrink-0 rounded-full p-1 text-muted-foreground hover:text-destructive"
                            onClick={() => pressPolicy("unpublish", { name: item.name })}
                            disabled={busy !== null}
                            aria-label={policyActionLabel("unpublish")}
                            data-testid={`dev-agi-policy-published-${item.name}-unpublish`}
                          >
                            <Trash2 className="h-3.5 w-3.5" aria-hidden />
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {policyOn && policy.recent.length ? (
                  <ul className="flex flex-wrap gap-1 px-1" data-testid="dev-agi-policy-recent">
                    {policy.recent.slice(0, 8).map((row, index) => (
                      <li
                        key={`${row.id}-${index}`}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-full bg-muted/70 px-2 py-px text-[10.5px]",
                          row.reward > 0 ? "text-muted-foreground" : VERDICT_TONE.down,
                        )}
                        title={`${row.case} (${row.split}) after ${row.prev.join(" > ") || "start"}`}
                      >
                        <span className="max-w-[8rem] truncate">{row.intent}</span>
                        <span className="font-semibold">{row.action}</span>
                        <span>{row.obs}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <AnimatePresence initial={false}>
                  {policyOn && policyJournalOpen ? (
                    <motion.ul
                      key="policy-journal"
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.18 }}
                      className="overflow-hidden rounded-2xl border border-border/55 bg-card/40 divide-y divide-border/40"
                      data-testid="dev-agi-policy-journal"
                    >
                      {[...policy.journal].reverse().slice(0, 12).map((entry, index) => (
                        <li key={`${entry.ts}-${index}`} className="flex flex-wrap items-baseline gap-x-2 px-4 py-1.5 text-[11.5px] leading-5">
                          <span className="tabular-nums text-muted-foreground/70">{String(entry.ts).replace("T", " ").replace("Z", "")}</span>
                          <span className="font-semibold">{String(entry.event)}</span>
                          {"checkpoint" in entry && typeof entry.checkpoint === "number" ? (
                            <span className="text-muted-foreground">#{entry.checkpoint}</span>
                          ) : null}
                          {"verdict" in entry && typeof entry.verdict === "string" ? (
                            <span className={VERDICT_TONE[(entry.verdict as "up" | "flat" | "down") ?? "flat"] ?? ""}>{String(entry.verdict)}</span>
                          ) : null}
                          {"reason" in entry && entry.reason ? (
                            <span className="truncate text-muted-foreground/80">{String(entry.reason)}</span>
                          ) : null}
                        </li>
                      ))}
                    </motion.ul>
                  ) : null}
                </AnimatePresence>
              </div>
            ) : null}
          </Section>

          <Section
            index={5}
            title={tx("dev.agi.transfer.section", "Transfer protocol")}
            hint={
              transfer
                ? transferOn
                  ? t("dev.agi.transfer.claimHint", {
                      defaultValue: "Transfer {{transfer}}, safety {{safety}}, claim {{claim}}. The protocol answers; nobody self-declares.",
                      transfer: transfer.claim.transfer,
                      safety: transfer.claim.safety,
                      claim: transfer.claim.status,
                    })
                  : tx(
                      "dev.agi.transfer.hint",
                      "Off by default, and off everywhere today. This is a hidden exam plus a shutdown dossier, not a mode: nothing here touches a chat turn. The claim stays forbidden until a secret campaign passes all four families and the safety dossier is green with steer on.",
                    )
                : tx("dev.agi.transfer.unavailable", "Transfer settings are not available on this gateway.")
            }
            action={
              transfer && transferOn && transfer.journal.length ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-6 gap-1.5 rounded-full px-2 text-[11.5px]"
                  onClick={() => setTransferJournalOpen((open) => !open)}
                  data-testid="dev-agi-transfer-journal-toggle"
                >
                  <History className="h-3 w-3" aria-hidden />
                  {transferJournalOpen ? tx("dev.agi.hideJournal", "Hide journal") : tx("dev.agi.showJournal", "Journal")}
                </Button>
              ) : null
            }
            testId="dev-agi-transfer"
          >
            <Card>
              <GuardrailRow
                testId="dev-agi-transfer-enabled"
                emphasis
                checked={transferOn}
                onChange={(next) => void saveTransfer("enabled", next)}
                disabled={saving || !transfer || transferLocked}
                icon={transfer && transferLocked ? Lock : EyeOff}
                title={tx("dev.agi.transfer.enabled", "Transfer protocol")}
                detail={
                  transfer && transferLocked
                    ? t("dev.agi.transfer.enabledLocked", {
                        defaultValue: "Locked: {{reasons}}. It unlocks by itself once a skill draft has passed its exam, the world model exam says up and a policy adapter has beaten its predecessor. Without them a campaign is a demo.",
                        reasons: transfer.prereqs.reasons.join("; "),
                      })
                    : tx(
                        "dev.agi.transfer.enabledDetail",
                        "Master switch. Off: no secret suite is drawn, no dossier is written, no claim is computed, no autonomy widens. On: the human may freeze the suites (CLI), run the campaign in a child process, run the safety dossier and the kill drill. Never attached to the chat. Stored in .navin/transfer.json.",
                      )
                }
              />
            </Card>
            {transfer ? (
              <div className="space-y-2" data-testid="dev-agi-transfer-stats">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11.5px] leading-5 text-muted-foreground">
                  <span
                    className={cn("inline-flex items-center gap-1.5", transfer.prereqs.ok ? VERDICT_TONE.up : "")}
                    data-testid="dev-agi-transfer-prereqs"
                    data-ok={transfer.prereqs.ok ? "true" : "false"}
                  >
                    {transfer.prereqs.ok ? <ShieldCheck className="h-3 w-3" aria-hidden /> : <Lock className="h-3 w-3" aria-hidden />}
                    {transfer.prereqs.ok
                      ? tx("dev.agi.transfer.prereqsOk", "A skill draft passed its exam, the world model exam is up, a policy adapter beat its predecessor")
                      : t("dev.agi.transfer.prereqsMissing", {
                          defaultValue: "Prerequisites: {{reasons}}",
                          reasons: transfer.prereqs.reasons.join(" / "),
                        })}
                  </span>
                  {transferOn && transfer.suites ? (
                    <span
                      className={cn("inline-flex items-center gap-1.5", transfer.suites.tampered ? VERDICT_TONE.down : "")}
                      data-testid="dev-agi-transfer-suites"
                      data-frozen={transfer.suites.frozen ? "true" : "false"}
                    >
                      <EyeOff className="h-3 w-3 shrink-0" aria-hidden />
                      {transfer.suites.tampered
                        ? t("dev.agi.transfer.suitesTampered", {
                            defaultValue: "Suites changed since the lock: campaigns void. {{reason}}",
                            reason: transfer.suites.tampered,
                          })
                        : transfer.suites.frozen
                          ? t("dev.agi.transfer.suitesFrozen", {
                              defaultValue: "Secret suites {{version}} frozen ({{counts}}), outside every repository",
                              version: transfer.suites.version,
                              counts: Object.entries(transfer.suites.families)
                                .map(([family, n]) => `${family} ${n}`)
                                .join(", "),
                            })
                          : Object.keys(transfer.suites.families).length
                            ? t("dev.agi.transfer.suitesUnfrozen", {
                                defaultValue: "Secret suites present ({{counts}}) but not frozen: navin agi transfer freeze",
                                counts: Object.entries(transfer.suites.families)
                                  .map(([family, n]) => `${family} ${n}`)
                                  .join(", "),
                              })
                            : t("dev.agi.transfer.suitesMissing", {
                                defaultValue: "No secret suites in {{dir}} (items never live in this repository)",
                                dir: transfer.suites.dir,
                              })}
                      {!transfer.suites.outside ? (
                        <span className={VERDICT_TONE.down} data-testid="dev-agi-transfer-placement">
                          {transfer.suites.placement_error}
                        </span>
                      ) : null}
                    </span>
                  ) : null}
                  {transferOn && transfer.campaign ? (
                    <span className="inline-flex items-center gap-1.5 tabular-nums" data-testid="dev-agi-transfer-campaign">
                      <VerdictIcon
                        verdict={claimTone(transfer.campaign.verdict)}
                        className={cn("h-3.5 w-3.5", VERDICT_TONE[claimTone(transfer.campaign.verdict)])}
                      />
                      {t("dev.agi.transfer.campaign", {
                        defaultValue: "Campaign {{id}}: {{verdict}}",
                        id: transfer.campaign.id,
                        verdict: transfer.campaign.verdict,
                      })}
                      <span className="text-muted-foreground" data-testid="dev-agi-transfer-families">
                        {familyScoresLabel(transfer.campaign.families)}
                      </span>
                      {transfer.campaign.stopped_at ? (
                        <span className={VERDICT_TONE.down}>
                          {t("dev.agi.transfer.stoppedAt", { defaultValue: "stopped at {{family}}", family: transfer.campaign.stopped_at })}
                        </span>
                      ) : null}
                    </span>
                  ) : null}
                  {transferOn && transfer.safety ? (
                    <span
                      className={cn("inline-flex items-center gap-1.5", transfer.safety.ok ? VERDICT_TONE.up : VERDICT_TONE.down)}
                      data-testid="dev-agi-transfer-safety"
                      data-ok={transfer.safety.ok ? "true" : "false"}
                    >
                      <ShieldAlert className="h-3 w-3" aria-hidden />
                      {transfer.safety.ok
                        ? t("dev.agi.transfer.safetyGreen", {
                            defaultValue: "Safety dossier green ({{n}} checks, {{mode}})",
                            n: transfer.safety.checks.length,
                            mode: transfer.safety.mode.steer_on ? "steer on" : "steer off",
                          })
                        : t("dev.agi.transfer.safetyHoles", {
                            defaultValue: "Safety holes: {{holes}}",
                            holes: transfer.safety.holes.join(" / "),
                          })}
                      {transfer.safety.drill ? (
                        <span className={transfer.safety.drill.ok ? "" : VERDICT_TONE.down} data-testid="dev-agi-transfer-drill">
                          {transfer.safety.drill.ok
                            ? tx("dev.agi.transfer.drillOk", "kill drill ok, relaunch manual")
                            : tx("dev.agi.transfer.drillHoles", "kill drill has holes")}
                        </span>
                      ) : null}
                    </span>
                  ) : null}
                  <span
                    className={cn("inline-flex items-center gap-1.5 font-semibold", VERDICT_TONE[claimTone(transfer.claim.status)])}
                    data-testid="dev-agi-transfer-claim"
                    data-claim={transfer.claim.status}
                  >
                    <Ban className="h-3 w-3" aria-hidden />
                    {transfer.claim.status === "discussable"
                      ? tx("dev.agi.transfer.claimDiscussable", "Claim discussable: both gates pass. Still not a word the product writes.")
                      : tx("dev.agi.transfer.claimForbidden", "Claim forbidden")}
                    {transfer.claim.reasons.length ? (
                      <span className="font-normal text-muted-foreground" data-testid="dev-agi-transfer-claim-reasons">
                        {transfer.claim.reasons.join(" / ")}
                      </span>
                    ) : null}
                  </span>
                </div>
                {transferOn ? (
                  <div className="flex flex-wrap items-center gap-1 px-1" data-testid="dev-agi-transfer-actions">
                    {transferButtons.map((action) => (
                      <ActionButton
                        key={action}
                        icon={transferActionIcon(action)}
                        label={transferActionLabel(action)}
                        onClick={() => pressTransfer(action, action === "replay" ? { name: transfer.campaign?.id ?? undefined } : {})}
                        disabled={busy !== null}
                        tone={action === "campaign" ? "primary" : action === "kill_drill" ? "danger" : "ghost"}
                        testId={`dev-agi-transfer-action-${action}`}
                      />
                    ))}
                    {busy?.startsWith("transfer:") ? <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" aria-hidden /> : null}
                  </div>
                ) : null}
                <AnimatePresence initial={false}>
                  {transferOn && transferJournalOpen ? (
                    <motion.ul
                      key="transfer-journal"
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      transition={{ duration: 0.18 }}
                      className="overflow-hidden rounded-2xl border border-border/55 bg-card/40 divide-y divide-border/40"
                      data-testid="dev-agi-transfer-journal"
                    >
                      {transfer.journal.slice(0, 12).map((entry, index) => (
                        <li key={`${entry.ts}-${index}`} className="flex flex-wrap items-baseline gap-x-2 px-4 py-1.5 text-[11.5px] leading-5">
                          <span className="tabular-nums text-muted-foreground/70">{String(entry.ts).replace("T", " ").replace("Z", "")}</span>
                          <span className="font-semibold">{String(entry.event)}</span>
                          {"verdict" in entry && typeof entry.verdict === "string" ? (
                            <span className={VERDICT_TONE[claimTone(entry.verdict)]}>{String(entry.verdict)}</span>
                          ) : null}
                          {"reason" in entry && entry.reason ? (
                            <span className="truncate text-muted-foreground/80">{String(entry.reason)}</span>
                          ) : null}
                        </li>
                      ))}
                    </motion.ul>
                  ) : null}
                </AnimatePresence>
              </div>
            ) : null}
          </Section>

          <Section
            index={6}
            title={tx("dev.agi.memorySection", "Memory")}
            hint={
              cognition
                ? memoryOn
                  ? journalSize
                    ? t("dev.agi.memoryJournalSize", {
                        defaultValue: "Journal: {{size}}",
                        size: journalSize,
                      })
                    : null
                  : tx(
                      "dev.agi.memoryHint",
                      "Off by default. Nothing is journaled and the agent has no recall tool until you turn it on here.",
                    )
                : tx(
                    "dev.agi.memoryUnavailable",
                    "Memory settings are not available on this gateway.",
                  )
            }
            testId="dev-agi-memory"
          >
            <Card>
              <GuardrailRow
                testId="dev-agi-cognition-enabled"
                emphasis
                checked={memoryOn}
                onChange={(next) => void saveCognition("enabled", next)}
                disabled={saving || !cognition}
                icon={Brain}
                title={tx("dev.agi.memory", "Episodic memory")}
                detail={tx(
                  "dev.agi.memoryDetail",
                  "Remember past turns of this project so the agent can look them up instead of redoing them. Stored in .navin/memory/episodes.jsonl, a few hundred bytes per turn, never sent anywhere.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-cognition-episodes"
                checked={cognition?.episodes ?? false}
                onChange={(next) => void saveCognition("episodes", next)}
                disabled={saving || !cognition || !memoryOn}
                icon={History}
                title={tx("dev.agi.episodes", "Journal each turn")}
                detail={tx(
                  "dev.agi.episodesDetail",
                  "After the answer: date, channel, desk, your request, the reply and the tools used. Written in the background, starts with your next turn. Heartbeat and internal turns are skipped.",
                )}
              />
              <GuardrailRow
                testId="dev-agi-cognition-recall"
                checked={cognition?.recall ?? false}
                onChange={(next) => void saveCognition("recall", next)}
                disabled={saving || !cognition || !memoryOn}
                icon={Search}
                title={tx("dev.agi.recall", "recall tool")}
                detail={tx(
                  "dev.agi.recallDetail",
                  "Gives the agent a read-only search over the journal and MEMORY.md ('like last time', 'the one we did'). Bounded results, off on the heartbeat.",
                )}
              />
            </Card>
            <AnimatePresence initial={false}>
              {recallRestartNoteVisible(cognition) ? (
                <motion.div
                  key="recall-restart"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.18 }}
                  className="overflow-hidden"
                  data-testid="dev-agi-recall-restart"
                >
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 pt-1 text-[11.5px] leading-5 text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <RotateCcw className="h-3 w-3 shrink-0" aria-hidden />
                      {tx(
                        "dev.agi.recallRestartNote",
                        "The agent does not have the recall tool yet: it joins after the next gateway restart. The journal does not wait.",
                      )}
                    </span>
                    {onOpenSettings ? (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-6 rounded-full px-2 text-[11.5px]"
                        onClick={onOpenSettings}
                        data-testid="dev-agi-recall-restart-link"
                      >
                        {tx("dev.agi.restartFromSettings", "Restart from Settings")}
                      </Button>
                    ) : null}
                  </div>
                </motion.div>
              ) : recallLiveNoteVisible(cognition) ? (
                <motion.p
                  key="recall-live"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.18 }}
                  className="overflow-hidden px-1 pt-1 text-[11.5px] leading-5 text-emerald-700 dark:text-emerald-300"
                  data-testid="dev-agi-recall-live"
                >
                  {tx(
                    "dev.agi.recallLiveNote",
                    "The agent has the recall tool now, no restart needed.",
                  )}
                </motion.p>
              ) : null}
            </AnimatePresence>
          </Section>

          <p className="px-1 pb-2 text-[11.5px] leading-5 text-muted-foreground">
            {tx(
              "dev.agi.footnote",
              "Project switches live in .navin/skills-evolve.json, .navin/world-model.json, .navin/policy.json, .navin/transfer.json and .navin/cognition.json. Exams, training and campaigns never run inside a chat turn: they run after it on the job runner or in a child process, from navin agi run / navin agi world train / navin agi policy train / navin agi transfer campaign, or from a cron. The same switches exist in the terminal: navin agi status.",
            )}
          </p>
        </div>
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title={confirm ? confirmCopy(confirm).title : ""}
        description={confirm ? confirmCopy(confirm).description : ""}
        confirmLabel={confirm ? actionLabel(confirm.action) : ""}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (!confirm) return;
          const entry = confirm;
          setConfirm(null);
          void runAction(entry.action, entry.name);
        }}
      />
      <ConfirmDialog
        open={worldConfirm !== null}
        title={worldConfirm ? worldConfirmCopy(worldConfirm).title : ""}
        description={worldConfirm ? worldConfirmCopy(worldConfirm).description : ""}
        confirmLabel={worldConfirm ? worldActionLabel(worldConfirm) : ""}
        onCancel={() => setWorldConfirm(null)}
        onConfirm={() => {
          if (!worldConfirm) return;
          const action = worldConfirm;
          setWorldConfirm(null);
          void runWorldAction(action);
        }}
      />
      <ConfirmDialog
        open={policyConfirm !== null}
        title={policyConfirm ? policyConfirmCopy(policyConfirm).title : ""}
        description={policyConfirm ? policyConfirmCopy(policyConfirm).description : ""}
        confirmLabel={policyConfirm ? policyActionLabel(policyConfirm.action) : ""}
        onCancel={() => setPolicyConfirm(null)}
        onConfirm={() => {
          if (!policyConfirm) return;
          const entry = policyConfirm;
          setPolicyConfirm(null);
          void runPolicyAction(entry.action, { name: entry.name, number: entry.number });
        }}
      />
      <ConfirmDialog
        open={transferConfirm !== null}
        title={transferConfirm ? transferConfirmCopy(transferConfirm).title : ""}
        description={transferConfirm ? transferConfirmCopy(transferConfirm).description : ""}
        confirmLabel={transferConfirm ? transferActionLabel(transferConfirm.action) : ""}
        onCancel={() => setTransferConfirm(null)}
        onConfirm={() => {
          if (!transferConfirm) return;
          const entry = transferConfirm;
          setTransferConfirm(null);
          void runTransferAction(entry.action, { name: entry.name });
        }}
      />
    </div>
  );
}

export default DevAgiPanel;
