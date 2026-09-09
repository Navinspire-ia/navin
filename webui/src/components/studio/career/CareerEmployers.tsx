// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { DefaultButton, IconButton, PrimaryButton, TextField, Toggle } from "@fluentui/react";
import "@/lib/fluent-icons";

import {
  BUTTON_STYLES,
  ICON_BUTTON_STYLES,
  SPRING,
  openOfficialCareerUrl,
  type Tx,
} from "@/components/studio/career/career-ui";
import type { CareerEmployerRow, CareerEmployers, CareerUserEmployer } from "@/lib/career-api";
import { cn } from "@/lib/utils";

export const ATS_LABELS: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  workable: "Workable",
  recruitee: "Recruitee",
  teamtailor: "Teamtailor",
  personio: "Personio",
  workday: "Workday",
  "careers-page": "Careers page",
  seed: "Feed ready",
};

export type EmployerFeedState = "ready" | "page" | "error" | "pending";

/** Reading state of one house: a detected ATS, a plain careers page, a failed probe, or not probed yet. */
export function employerFeedState(row: CareerEmployerRow): EmployerFeedState {
  const ats = (row.ats || "").toLowerCase();
  if (!ats) return row.last_error ? "error" : "pending";
  if (ats === "careers-page") return row.last_error ? "error" : "page";
  return row.last_error ? "error" : "ready";
}

/** Directory rows first by kind, user rows on top, hidden rows last. */
export function sortEmployerRows(rows: CareerEmployerRow[]): CareerEmployerRow[] {
  const kindRank: Record<string, number> = { esn: 0, consulting: 1, big4: 2, agency: 3, product: 4 };
  return [...rows].sort((a, b) => {
    if (Boolean(a.hidden) !== Boolean(b.hidden)) return a.hidden ? 1 : -1;
    if (Boolean(a.user) !== Boolean(b.user)) return a.user ? -1 : 1;
    const rank = (kindRank[a.kind] ?? 9) - (kindRank[b.kind] ?? 9);
    if (rank !== 0) return rank;
    return a.name.localeCompare(b.name);
  });
}

/** "Name | https://careers.example.com" or a bare URL. Returns null when neither name nor URL is usable. */
export function parseEmployerInput(name: string, url: string): CareerUserEmployer | null {
  let cleanName = name.trim();
  let cleanUrl = url.trim();
  if (!cleanUrl && cleanName.includes("|")) {
    const [left, right] = cleanName.split("|");
    cleanName = (left || "").trim();
    cleanUrl = (right || "").trim();
  }
  if (cleanUrl && !/^https?:\/\//i.test(cleanUrl)) cleanUrl = `https://${cleanUrl}`;
  if (!cleanName && cleanUrl) {
    try {
      cleanName = new URL(cleanUrl).hostname.replace(/^www\./, "");
    } catch {
      return null;
    }
  }
  if (!cleanName && !cleanUrl) return null;
  if (cleanUrl) {
    try {
      new URL(cleanUrl);
    } catch {
      return null;
    }
  }
  return { name: cleanName, url: cleanUrl };
}

const KIND_LABEL: Record<string, string> = {
  esn: "ESN",
  consulting: "Consulting",
  big4: "Big 4",
  agency: "Agency",
  product: "Product",
};

const STATE_CLASS: Record<EmployerFeedState, string> = {
  ready: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-200",
  page: "bg-sky-500/15 text-sky-800 dark:text-sky-200",
  error: "bg-amber-500/15 text-amber-900 dark:text-amber-200",
  pending: "bg-muted text-muted-foreground",
};

export function CareerEmployers({
  tx,
  employers,
  userEmployers,
  hidden,
  token,
  busy,
  onSaveProfile,
}: {
  tx: Tx;
  employers?: CareerEmployers;
  userEmployers: CareerUserEmployer[];
  hidden: string[];
  token: string;
  busy?: boolean;
  onSaveProfile: (patch: Record<string, unknown>) => void;
}) {
  const reduceMotion = useReducedMotion();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [open, setOpen] = useState(false);
  const [formError, setFormError] = useState("");
  const rows = useMemo(() => sortEmployerRows(employers?.rows || []), [employers?.rows]);
  const enabled = employers?.enabled !== false;
  const summary = employers?.summary;
  const ready = rows.filter((row) => !row.hidden && employerFeedState(row) === "ready").length;
  const postings = rows.reduce((sum, row) => sum + (row.hidden ? 0 : row.last_count || 0), 0);
  const visible = rows.filter((row) => !row.hidden);
  const hiddenRows = rows.filter((row) => row.hidden);

  const stateLabel = (state: EmployerFeedState) =>
    state === "ready"
      ? tx("employersReady", "Feed ready")
      : state === "page"
        ? tx("employersPage", "Careers page")
        : state === "error"
          ? tx("employersError", "Check failed")
          : tx("employersPending", "Not checked yet");

  const addEmployer = () => {
    const parsed = parseEmployerInput(name, url);
    if (!parsed) {
      setFormError(tx("employersInvalid", "Give a company name and a careers URL (https://...)."));
      return;
    }
    const next = [...userEmployers.filter((row) => row.name.toLowerCase() !== parsed.name.toLowerCase()), parsed];
    onSaveProfile({ employers: next });
    setName("");
    setUrl("");
    setFormError("");
  };

  const removeUser = (row: CareerEmployerRow) => {
    onSaveProfile({ employers: userEmployers.filter((item) => item.name.toLowerCase() !== row.name.toLowerCase()) });
  };

  const toggleHidden = (row: CareerEmployerRow) => {
    const set = new Set(hidden.map((item) => item.toLowerCase()));
    if (set.has(row.id)) set.delete(row.id);
    else set.add(row.id);
    onSaveProfile({ employers_hidden: [...set] });
  };

  return (
    <div className="space-y-4" data-testid="career-employers">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold">{tx("employersTitle", "ESN, consulting and agency feeds")}</p>
          <p className="mt-1 max-w-xl text-pretty text-sm text-muted-foreground">
            {tx(
              "employersBody",
              "Navin reads the job boards of the houses hiring in your markets straight from their ATS (Greenhouse, Lever, SmartRecruiters, Workday, ...) or careers page. Add your own targets below; every posting stays attributed to the employer.",
            )}
          </p>
        </div>
        <Toggle
          checked={enabled}
          disabled={busy}
          onText={tx("employersOn", "Watching")}
          offText={tx("employersOff", "Paused")}
          onChange={(_, checked) => onSaveProfile({ employer_watch: checked !== false })}
          styles={{ root: { marginBottom: 0 } }}
          data-testid="career-employers-toggle"
        />
      </div>

      <dl className="flex flex-wrap gap-x-5 gap-y-2 text-sm" data-testid="career-employers-summary">
        <div>
          <dt className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{tx("employersHouses", "Houses")}</dt>
          <dd className="font-semibold tabular-nums">{summary?.directory ?? visible.length}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{tx("employersFeeds", "Feeds ready")}</dt>
          <dd className="font-semibold tabular-nums">{ready}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{tx("employersMine", "Yours")}</dt>
          <dd className="font-semibold tabular-nums">{userEmployers.length}</dd>
        </div>
        <div>
          <dt className="text-[11px] uppercase tracking-[0.12em] text-muted-foreground">{tx("employersPostings", "Postings last run")}</dt>
          <dd className="font-semibold tabular-nums">{postings}</dd>
        </div>
      </dl>

      <div className="grid gap-2 sm:grid-cols-[1fr_1.4fr_auto] sm:items-end" data-testid="career-employers-add">
        <TextField
          label={tx("employersName", "Company")}
          value={name}
          onChange={(_, value) => setName(value || "")}
          placeholder="Capgemini Invent"
        />
        <TextField
          label={tx("employersUrl", "Careers page or ATS URL")}
          value={url}
          onChange={(_, value) => setUrl(value || "")}
          placeholder="https://jobs.lever.co/..."
          onKeyDown={(event) => {
            if (event.key === "Enter") addEmployer();
          }}
        />
        <PrimaryButton
          text={tx("employersAdd", "Watch")}
          iconProps={{ iconName: "Add" }}
          onClick={addEmployer}
          disabled={busy || (!name.trim() && !url.trim())}
          styles={BUTTON_STYLES}
          data-testid="career-employers-add-button"
        />
      </div>
      {formError ? (
        <p className="text-sm text-amber-700 dark:text-amber-300" role="alert">
          {formError}
        </p>
      ) : null}

      <div>
        <DefaultButton
          text={
            open
              ? tx("employersHide", "Hide the list")
              : tx("employersShow", "Show the {{count}} houses", { count: visible.length })
          }
          iconProps={{ iconName: open ? "ChevronUp" : "ChevronDown" }}
          onClick={() => setOpen((current) => !current)}
          styles={BUTTON_STYLES}
          data-testid="career-employers-show"
        />
      </div>
      <AnimatePresence initial={false}>
        {open ? (
          <motion.ul
            key="employer-list"
            initial={reduceMotion ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
            transition={SPRING}
            className="grid gap-1 overflow-hidden"
            data-testid="career-employers-list"
          >
            {[...visible, ...hiddenRows].map((row) => {
              const state = employerFeedState(row);
              const ats = (row.ats || "").toLowerCase();
              return (
                <li
                  key={row.id}
                  className={cn(
                    "flex min-w-0 flex-wrap items-center gap-2 rounded-lg px-2 py-1.5 text-sm outline outline-1 outline-black/5 dark:outline-white/5",
                    row.hidden && "opacity-55",
                  )}
                  data-employer-id={row.id}
                >
                  <button
                    type="button"
                    className="min-w-0 max-w-[16rem] truncate text-left font-medium text-indigo-700 underline-offset-2 hover:underline dark:text-indigo-300"
                    onClick={() => row.careers_url && openOfficialCareerUrl(token, row.careers_url)}
                    title={row.careers_url || row.name}
                  >
                    {row.name}
                  </button>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                    {KIND_LABEL[row.kind] || row.kind}
                  </span>
                  <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", STATE_CLASS[state])}>
                    {ats && ats !== "seed" && state !== "error" ? ATS_LABELS[ats] || ats : stateLabel(state)}
                  </span>
                  {row.last_count ? (
                    <span className="text-[12px] tabular-nums text-muted-foreground">
                      {tx("employersCount", "{{count}} postings", { count: row.last_count })}
                    </span>
                  ) : null}
                  {row.last_error && state === "error" ? (
                    <span className="truncate text-[12px] text-amber-700 dark:text-amber-300" title={row.last_error}>
                      {row.last_error}
                    </span>
                  ) : null}
                  <span className="ml-auto flex items-center gap-1">
                    <span className="text-[11px] text-muted-foreground">{row.markets.slice(0, 4).join(" ")}</span>
                    {row.user ? (
                      <IconButton
                        iconProps={{ iconName: "Delete" }}
                        title={tx("employersRemove", "Stop watching")}
                        ariaLabel={tx("employersRemove", "Stop watching")}
                        onClick={() => removeUser(row)}
                        styles={ICON_BUTTON_STYLES}
                      />
                    ) : (
                      <IconButton
                        iconProps={{ iconName: row.hidden ? "View" : "Hide3" }}
                        title={row.hidden ? tx("employersUnhide", "Watch again") : tx("employersMute", "Mute this house")}
                        ariaLabel={row.hidden ? tx("employersUnhide", "Watch again") : tx("employersMute", "Mute this house")}
                        onClick={() => toggleHidden(row)}
                        styles={ICON_BUTTON_STYLES}
                      />
                    )}
                  </span>
                </li>
              );
            })}
          </motion.ul>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
