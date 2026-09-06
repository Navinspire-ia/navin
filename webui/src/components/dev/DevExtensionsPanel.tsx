import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  AlertTriangle,
  Blocks,
  Check,
  Download,
  ExternalLink,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  fetchLspServers,
  installDetectedLspServer,
  installLspServer,
  searchLspMarketplace,
  uninstallLspServer,
  type LspServerEntry,
  type LspServersPayload,
  type MarketplaceExtension,
} from "@/lib/api";
import { cn } from "@/lib/utils";

/** Long enough that typing a name is one request, short enough to feel live. */
const SEARCH_DEBOUNCE_MS = 400;

/** Where the marketplace entry for an extension lives, for the "view" link. */
function openVsxUrl(marketplace: string): string {
  return `https://open-vsx.org/extension/${marketplace}`;
}

function downloadCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}k`;
  return String(value);
}

/**
 * The extension's own icon, or its initial when there is none.
 *
 * Only the registry knows an icon URL, so entries that came from the installed
 * table have none. A letter keeps the rows aligned instead of leaving a hole,
 * and it also covers an icon that fails to load with no network.
 */
function ExtensionIcon({ src, name }: { src?: string; name: string }) {
  const [broken, setBroken] = useState(false);
  const showImage = Boolean(src) && !broken;

  return (
    <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-md border border-border/50 bg-muted/40">
      {showImage ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          className="h-full w-full object-contain"
          onError={() => setBroken(true)}
        />
      ) : (
        <span className="text-[11px] font-semibold uppercase text-muted-foreground">
          {name.trim().charAt(0) || "?"}
        </span>
      )}
    </span>
  );
}

function ExtensionRow({
  entry,
  busy,
  disabled,
  onInstall,
  onRemove,
}: {
  entry: LspServerEntry;
  busy: boolean;
  disabled: boolean;
  onInstall: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const suffixes = entry.suffixes.join(" ");

  return (
    <li className="group flex flex-col gap-1.5 px-2 py-2">
      <div className="flex items-start gap-2">
        <ExtensionIcon name={entry.displayName} />
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 truncate text-[12px] font-medium text-foreground">
            {entry.displayName}
            {entry.installed && entry.version ? (
              <span className="shrink-0 font-normal text-[10px] text-muted-foreground">
                v{entry.version}
              </span>
            ) : null}
          </p>
          {suffixes ? (
            <p className="truncate font-mono text-[10.5px] text-muted-foreground">
              {suffixes}
            </p>
          ) : null}
        </div>

        {entry.installed ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 shrink-0 px-2 text-[11px] text-muted-foreground hover:text-red-500"
            disabled={busy || disabled}
            onClick={onRemove}
            title={t("dev.extensions.remove", { defaultValue: "Remove" })}
          >
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <Trash2 className="h-3 w-3" aria-hidden />
            )}
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            className="h-7 shrink-0 gap-1 px-2 text-[11px]"
            disabled={busy || disabled}
            onClick={onInstall}
          >
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <Download className="h-3 w-3" aria-hidden />
            )}
            {t("dev.extensions.install", { defaultValue: "Install" })}
          </Button>
        )}
      </div>

      {entry.installed && !entry.ready ? (
        <p className="flex items-start gap-1 text-[10.5px] text-amber-600 dark:text-amber-400">
          <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden />
          <span className="min-w-0">
            {entry.detail ||
              t("dev.extensions.notReady", {
                defaultValue: "Installed, but it cannot start on this machine.",
              })}
          </span>
        </p>
      ) : entry.installed ? (
        <p className="flex items-center gap-1 text-[10.5px] text-emerald-600 dark:text-emerald-400">
          <Check className="h-3 w-3 shrink-0" aria-hidden />
          {t("dev.extensions.active", { defaultValue: "Active in the editor" })}
        </p>
      ) : entry.notes ? (
        <p className="text-[10.5px] leading-4 text-muted-foreground">{entry.notes}</p>
      ) : null}

      {entry.marketplace ? (
        <a
          href={openVsxUrl(entry.marketplace)}
          target="_blank"
          rel="noreferrer"
          className="inline-flex w-fit items-center gap-1 font-mono text-[10px] text-muted-foreground opacity-0 transition-opacity hover:text-foreground hover:underline group-hover:opacity-100 focus-visible:opacity-100"
        >
          {entry.marketplace}
          <ExternalLink className="h-2.5 w-2.5" aria-hidden />
        </a>
      ) : null}
    </li>
  );
}

function MarketplaceRow({
  entry,
  busy,
  disabled,
  onInstall,
}: {
  entry: MarketplaceExtension;
  busy: boolean;
  disabled: boolean;
  onInstall: () => void;
}) {
  const { t } = useTranslation();

  return (
    <li className="group flex flex-col gap-1 px-2 py-2">
      <div className="flex items-start gap-2">
        <ExtensionIcon src={entry.icon} name={entry.displayName} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[12px] font-medium text-foreground">
            {entry.displayName}
          </p>
          <p className="flex items-center gap-1.5 truncate font-mono text-[10px] text-muted-foreground">
            {entry.slug}
            <span className="shrink-0">{downloadCount(entry.downloads)}</span>
          </p>
        </div>

        {entry.installed ? (
          <span className="flex h-7 shrink-0 items-center gap-1 px-1 text-[10.5px] text-emerald-600 dark:text-emerald-400">
            <Check className="h-3 w-3" aria-hidden />
            {t("dev.extensions.alreadyInstalled", { defaultValue: "Installed" })}
          </span>
        ) : (
          <Button
            type="button"
            size="sm"
            className="h-7 shrink-0 gap-1 px-2 text-[11px]"
            disabled={busy || disabled}
            onClick={onInstall}
          >
            {busy ? (
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            ) : (
              <Download className="h-3 w-3" aria-hidden />
            )}
            {t("dev.extensions.install", { defaultValue: "Install" })}
          </Button>
        )}
      </div>

      {entry.description ? (
        <p className="line-clamp-2 text-[10.5px] leading-4 text-muted-foreground">
          {entry.description}
        </p>
      ) : null}
    </li>
  );
}

/**
 * Install language servers without leaving the editor.
 *
 * The whole Open VSX registry is searchable here, but only the language server
 * inside an extension is used: Navin speaks LSP, not the VS Code extension API,
 * so an extension's commands and views do not come along. Rather than guess
 * which entries qualify, installing one downloads it, finds the server inside
 * and starts it: what is registered has answered an LSP handshake, and an
 * extension with nothing behind it is refused instead of silently doing
 * nothing.
 */
export function DevExtensionsPanel({
  token,
  sessionKey,
}: {
  token: string;
  sessionKey: string;
}) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const [payload, setPayload] = useState<LspServersPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MarketplaceExtension[] | null>(null);
  const [searching, setSearching] = useState(false);
  const requestId = useRef(0);
  const searchId = useRef(0);

  const load = useCallback(async () => {
    if (!token) return;
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const next = await fetchLspServers(token, sessionKey || null);
      if (id !== requestId.current) return;
      setPayload(next);
    } catch (err) {
      if (id !== requestId.current) return;
      setError(err instanceof Error ? err.message : "failed to load extensions");
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [sessionKey, token]);

  useEffect(() => {
    void load();
  }, [load]);

  const trimmed = query.trim();

  useEffect(() => {
    if (!token || trimmed.length < 2) {
      setResults(null);
      setSearching(false);
      return;
    }
    const id = ++searchId.current;
    setSearching(true);
    const timer = window.setTimeout(async () => {
      try {
        const found = await searchLspMarketplace(token, trimmed, sessionKey || null);
        if (id !== searchId.current) return;
        setResults(found.results);
      } catch (err) {
        if (id !== searchId.current) return;
        setError(err instanceof Error ? err.message : "search failed");
        setResults([]);
      } finally {
        if (id === searchId.current) setSearching(false);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [sessionKey, token, trimmed]);

  const run = useCallback(
    async (name: string, action: () => Promise<LspServersPayload>, done: string) => {
      setBusyName(name);
      setError(null);
      setMessage(null);
      try {
        setPayload(await action());
        setMessage(done);
      } catch (err) {
        setError(err instanceof Error ? err.message : "the operation failed");
      } finally {
        setBusyName(null);
      }
    },
    [],
  );

  const install = (entry: LspServerEntry) =>
    void run(
      entry.name,
      () => installLspServer(token, entry.name, sessionKey || null),
      t("dev.extensions.installed", {
        name: entry.displayName,
        defaultValue: "{{name}} is ready. Open a matching file to use it.",
      }),
    );

  const remove = (entry: LspServerEntry) =>
    void run(
      entry.name,
      () => uninstallLspServer(token, entry.name, sessionKey || null),
      t("dev.extensions.removed", {
        name: entry.displayName,
        defaultValue: "{{name}} removed.",
      }),
    );

  const installFound = (entry: MarketplaceExtension) =>
    void run(
      entry.slug,
      async () => {
        const next = await installDetectedLspServer(token, entry.slug, sessionKey || null);
        // The row has to stop offering an install it already carried out.
        setResults((prev) =>
          prev
            ? prev.map((row) =>
                row.slug === entry.slug ? { ...row, installed: true } : row,
              )
            : prev,
        );
        return next;
      },
      t("dev.extensions.installed", {
        name: entry.displayName,
        defaultValue: "{{name}} is ready. Open a matching file to use it.",
      }),
    );

  const rows = [...(payload?.extensions ?? []), ...(payload?.others ?? [])];
  const busy = busyName !== null;
  const browsing = results !== null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border/40 px-2 py-1.5">
        <Blocks className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-foreground">
          {t("dev.panel.extensions", { defaultValue: "Extensions" })}
        </span>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading || busy}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
          title={t("dev.extensions.refresh", { defaultValue: "Refresh" })}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} aria-hidden />
        </button>
      </div>

      <div className="relative shrink-0 px-2 py-2">
        {searching ? (
          <Loader2
            className="pointer-events-none absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-muted-foreground"
            aria-hidden
          />
        ) : (
          <Search
            className="pointer-events-none absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
        )}
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("dev.extensions.searchPlaceholder", {
            defaultValue: "Search Open VSX",
          })}
          className="h-8 pl-7 pr-7 text-[11px]"
          aria-label={t("dev.extensions.searchPlaceholder", {
            defaultValue: "Search Open VSX",
          })}
        />
        {query ? (
          <button
            type="button"
            onClick={() => setQuery("")}
            className="absolute right-4 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
            title={t("dev.extensions.clearSearch", { defaultValue: "Clear" })}
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {browsing ? (
          <p className="px-2 pb-1 text-[10.5px] leading-4 text-muted-foreground">
            {t("dev.extensions.searchHint", {
              defaultValue:
                "Installing checks the extension really carries a language server, so it takes a moment.",
            })}
          </p>
        ) : null}

        {payload && !payload.nodeAvailable ? (
          <p className="mx-2 mt-2 flex items-start gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[10.5px] leading-4 text-amber-700 dark:text-amber-300">
            <AlertTriangle className="mt-px h-3 w-3 shrink-0" aria-hidden />
            <span>
              {t("dev.extensions.noNode", {
                defaultValue:
                  "Node.js is not installed, so most of these cannot run. Install it, then refresh.",
              })}
            </span>
          </p>
        ) : null}

        <AnimatePresence initial={false}>
          {error ? (
            <motion.p
              key="error"
              initial={reduceMotion ? false : { opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
              className="overflow-hidden px-2 pt-2 text-[11px] leading-4 text-red-500"
            >
              {error}
            </motion.p>
          ) : message ? (
            <motion.p
              key="message"
              initial={reduceMotion ? false : { opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
              className="overflow-hidden px-2 pt-2 text-[11px] leading-4 text-emerald-600 dark:text-emerald-400"
            >
              {message}
            </motion.p>
          ) : null}
        </AnimatePresence>

        {browsing ? (
          results.length === 0 ? (
            <p className="px-2 py-3 text-[11px] text-muted-foreground">
              {searching
                ? t("dev.extensions.loading", { defaultValue: "Loading..." })
                : t("dev.extensions.noResults", {
                    defaultValue: "Nothing on Open VSX matches that.",
                  })}
            </p>
          ) : (
            <ul className="divide-y divide-border/40">
              {results.map((entry) => (
                <MarketplaceRow
                  key={entry.slug}
                  entry={entry}
                  busy={busyName === entry.slug}
                  disabled={busy && busyName !== entry.slug}
                  onInstall={() => installFound(entry)}
                />
              ))}
            </ul>
          )
        ) : loading && rows.length === 0 ? (
          <p className="flex items-center gap-1.5 px-2 py-3 text-[11px] text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
            {t("dev.extensions.loading", { defaultValue: "Loading..." })}
          </p>
        ) : (
          <ul className="divide-y divide-border/40">
            {rows.map((entry) => (
              <ExtensionRow
                key={entry.name}
                entry={entry}
                busy={busyName === entry.name}
                disabled={busy && busyName !== entry.name}
                onInstall={() => install(entry)}
                onRemove={() => remove(entry)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export default DevExtensionsPanel;
