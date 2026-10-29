// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * One connector's connection form, shared by Settings > Channels > Connectors
 * and the journal card the agent opens (request_credentials kind=connector).
 *
 * Keys and tokens go from these fields to /api/connectors, where Navin checks
 * them with the app before keeping them in the local vault. Only a status and
 * an account name come back; the chat socket never carries a value.
 */

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, Copy, ExternalLink, Loader2, LogIn, Plug, Power, RefreshCw, ShieldCheck, Unplug } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { openInOsBrowser } from "@/lib/api";
import { copyTextOrNotify } from "@/lib/clipboard";
import {
  connectConnector,
  type ConnectorDetail,
  type ConnectorField,
  type ConnectorMethod,
  type ConnectorStatus,
  disconnectConnector,
  errorMessage,
  fetchConnectorDetail,
  fetchConnectorStatus,
  saveConnectorOAuthApp,
  startConnectorOAuth,
  testConnector,
  toggleConnector,
} from "@/lib/connectors-api";
import { cn } from "@/lib/utils";
import { SEGMENT_GROUP_CLASS, segmentClass } from "./segmented";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 10 * 60 * 1000;
const spring = { type: "spring" as const, duration: 0.3, bounce: 0 };

type Busy = "connect" | "oauth" | "app" | "test" | "toggle" | "disconnect" | null;

export interface ConnectorSetupFormProps {
  token: string | null | undefined;
  service: string;
  /** Journal card: no turn off / disconnect, and "Use this connection" when already connected. */
  compact?: boolean;
  onConnected?: (account: string) => void;
  onStatusChange?: (service: string, status: ConnectorStatus) => void;
  onLoaded?: (detail: ConnectorDetail) => void;
}

function initialValues(method: ConnectorMethod | undefined, status?: ConnectorStatus): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of method?.fields ?? []) {
    out[field.key] = field.secret ? "" : (status?.fields?.[field.key] ?? field.defaultValue ?? "");
  }
  return out;
}

function missingField(method: ConnectorMethod, values: Record<string, string>): ConnectorField | undefined {
  return method.fields.find((field) => field.required && !field.defaultValue && !(values[field.key] ?? "").trim());
}

/** Sign-in first whenever the app offers it, the other methods in their order. */
export function oauthFirst(methods: readonly ConnectorMethod[]): ConnectorMethod[] {
  return [...methods.filter((item) => item.type === "oauth2"), ...methods.filter((item) => item.type !== "oauth2")];
}

export function ConnectorSetupForm({
  token,
  service,
  compact = false,
  onConnected,
  onStatusChange,
  onLoaded,
}: ConnectorSetupFormProps) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  // Handlers run after the `!token` early return below; a plain string keeps them typed.
  const apiToken = token ?? "";
  const tx = useCallback(
    (key: string, fallback: string, values?: Record<string, unknown>) =>
      String(t(`settings.connectors.form.${key}`, { defaultValue: fallback, ...(values ?? {}) })),
    [t],
  );
  const [detail, setDetail] = useState<ConnectorDetail | null>(null);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [methodType, setMethodType] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState("");
  // Server failures get a translated headline over the app's own words.
  const [errorFromServer, setErrorFromServer] = useState(false);
  const [notice, setNotice] = useState("");
  const [reconnect, setReconnect] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [app, setApp] = useState({ client_id: "", client_secret: "", redirect_uri: "" });
  const [editApp, setEditApp] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [authUrl, setAuthUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const pollRef = useRef<number | null>(null);
  const confirmRef = useRef<number | null>(null);
  const onLoadedRef = useRef(onLoaded);
  onLoadedRef.current = onLoaded;

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  useEffect(
    () => () => {
      stopPolling();
      if (confirmRef.current !== null) window.clearTimeout(confirmRef.current);
    },
    [stopPolling],
  );

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setLoadError("");
    fetchConnectorDetail(apiToken, service, controller.signal)
      .then((loaded) => {
        const data = { ...loaded, methods: oauthFirst(loaded.methods) };
        setDetail(data);
        const first = data.methods[0];
        setMethodType(first?.type ?? "");
        setValues(initialValues(first, data.status));
        if (data.oauth_app) {
          setApp({
            client_id: data.oauth_app.client_id,
            client_secret: "",
            redirect_uri: data.oauth_app.redirect_uri || data.oauth_app.suggested_redirect_uri,
          });
        }
        onLoadedRef.current?.(data);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setLoadError(errorMessage(err));
      });
    return () => controller.abort();
  }, [token, service, reload]);

  const applyStatus = useCallback(
    (status: ConnectorStatus) => {
      setDetail((current) => (current ? { ...current, status } : current));
      onStatusChange?.(service, status);
    },
    [onStatusChange, service],
  );

  if (!token) return null;
  if (loadError) {
    return (
      <div role="alert" className="space-y-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-[13px]">
        <p className="text-destructive">{loadError}</p>
        <Button type="button" size="sm" variant="outline" onClick={() => setReload((n) => n + 1)}>
          <RefreshCw className="me-1.5 h-3.5 w-3.5" aria-hidden />
          {tx("retry", "Try again")}
        </Button>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="flex h-24 items-center justify-center text-[13px] text-muted-foreground" role="status">
        <Loader2 className="me-2 h-4 w-4 animate-spin" aria-hidden />
        {tx("loading", "Loading the connection form...")}
      </div>
    );
  }

  const status = detail.status;
  const fail = (err: unknown) => {
    setError(errorMessage(err));
    setErrorFromServer(true);
  };
  const failLocal = (text: string) => {
    setError(text);
    setErrorFromServer(false);
  };
  const method = detail.methods.find((item) => item.type === methodType) ?? detail.methods[0];
  const oauthApp = detail.oauth_app;
  const setValue = (key: string, value: string) => setValues((current) => ({ ...current, [key]: value }));

  const chooseMethod = (next: ConnectorMethod) => {
    setMethodType(next.type);
    setValues(initialValues(next, status));
    setError("");
    setNotice("");
  };

  const submitKey = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!method || method.type === "oauth2" || busy) return;
    const missing = missingField(method, values);
    if (missing) {
      failLocal(tx("required", "{{label}} is required.", { label: missing.label }));
      return;
    }
    setBusy("connect");
    setError("");
    setNotice("");
    try {
      const result = await connectConnector(apiToken, service, method.type, values);
      setValues(initialValues(method, result.status));
      setReconnect(false);
      applyStatus(result.status);
      onConnected?.(result.status.account ?? "");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const saveApp = async (event?: FormEvent) => {
    event?.preventDefault();
    if (busy || !oauthApp) return;
    setBusy("app");
    setError("");
    try {
      const saved = await saveConnectorOAuthApp(apiToken, service, {
        client_id: app.client_id,
        client_secret: app.client_secret,
        redirect_uri: app.redirect_uri || oauthApp.suggested_redirect_uri,
      });
      setDetail((current) => (current ? { ...current, oauth_app: saved } : current));
      setApp((current) => ({ ...current, client_secret: "" }));
      setEditApp(false);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const startOAuth = async () => {
    if (!method || busy) return;
    const missing = missingField(method, values);
    if (missing) {
      failLocal(tx("required", "{{label}} is required.", { label: missing.label }));
      return;
    }
    setBusy("oauth");
    setError("");
    setAuthUrl("");
    try {
      const started = await startConnectorOAuth(apiToken, service, values);
      const opened = await openInOsBrowser(apiToken, started.authorization_url).catch(() => ({ opened: false }));
      if (!opened.opened) setAuthUrl(started.authorization_url);
      setWaiting(true);
      const since = Date.now();
      stopPolling();
      pollRef.current = window.setInterval(() => {
        if (Date.now() - since > POLL_LIMIT_MS) {
          stopPolling();
          setWaiting(false);
          failLocal(tx("timedOut", "The sign-in did not finish in time. Try again."));
          return;
        }
        fetchConnectorStatus(apiToken, service)
          .then(({ status: next }) => {
            // A reconnect is done once the stored connection is newer than the click.
            if (!next.connected || (next.connected_at ?? 0) * 1000 < since - 5000) return;
            stopPolling();
            setWaiting(false);
            setAuthUrl("");
            setReconnect(false);
            applyStatus(next);
            onConnected?.(next.account ?? "");
          })
          .catch(() => undefined);
      }, POLL_MS);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const runTest = async () => {
    if (busy) return;
    setBusy("test");
    setError("");
    setNotice("");
    try {
      const result = await testConnector(apiToken, service);
      applyStatus(result.status);
      setNotice(tx("testOk", "The connection works."));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const toggle = async () => {
    if (busy) return;
    setBusy("toggle");
    setError("");
    try {
      const result = await toggleConnector(apiToken, service, status.enabled === false);
      applyStatus(result.status);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const disconnect = async () => {
    if (busy) return;
    if (!confirmDisconnect) {
      setConfirmDisconnect(true);
      if (confirmRef.current !== null) window.clearTimeout(confirmRef.current);
      confirmRef.current = window.setTimeout(() => setConfirmDisconnect(false), 4000);
      return;
    }
    setConfirmDisconnect(false);
    setBusy("disconnect");
    setError("");
    setNotice("");
    try {
      const result = await disconnectConnector(apiToken, service);
      applyStatus(result.status);
      setValues(initialValues(method, result.status));
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  };

  const copyRedirect = async () => {
    const value = app.redirect_uri || oauthApp?.suggested_redirect_uri || "";
    if (value && (await copyTextOrNotify(value))) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }
  };

  const methodLabel = (item: ConnectorMethod) =>
    item.type === "oauth2"
      ? tx("methodOAuth", "Sign in")
      : item.type === "service_account"
        ? tx("methodServiceAccount", "Service account")
        : item.label;

  const fieldInputs = (fields: ConnectorField[]) =>
    fields.map((field) => (
      <FieldInput
        key={field.key}
        field={field}
        value={values[field.key] ?? ""}
        onChange={(value) => setValue(field.key, value)}
        optionalLabel={tx("optional", "optional")}
        idPrefix={`connector-${service}`}
      />
    ));

  const showConnected = status.connected && !reconnect;
  const needsApp = Boolean(oauthApp) && (!oauthApp?.configured || editApp);

  return (
    <div className="space-y-3 text-[13px]" data-testid={`connector-form-${service}`}>
      {showConnected ? (
        <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] p-3" data-testid="connector-connected">
          <p className="flex min-w-0 items-center gap-2 font-medium text-emerald-700 dark:text-emerald-300">
            <ShieldCheck className="h-4 w-4 shrink-0" aria-hidden />
            <span className="truncate" dir="auto">
              {status.account
                ? tx("connectedAs", "Connected as {{account}}", { account: status.account })
                : tx("connectedNoAccount", "Connected")}
            </span>
          </p>
          {status.enabled === false ? (
            <p className="mt-1 text-[12px] text-muted-foreground">
              {tx("offHint", "Turned off: the agent cannot use it until you turn it back on.")}
            </p>
          ) : null}
          {status.error ? (
            <p className="mt-1 text-[12px] text-destructive" dir="auto">
              {tx("lastError", "Last error: {{error}}", { error: status.error })}
            </p>
          ) : null}
          <div className="mt-2.5 flex flex-wrap gap-2">
            {compact ? (
              <Button type="button" size="sm" onClick={() => onConnected?.(status.account ?? "")} data-testid="connector-use">
                <Check className="me-1.5 h-3.5 w-3.5" aria-hidden />
                {tx("useConnection", "Use this connection")}
              </Button>
            ) : null}
            <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={runTest} data-testid="connector-test">
              {busy === "test" ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <RefreshCw className="me-1.5 h-3.5 w-3.5" aria-hidden />}
              {tx("test", "Test")}
            </Button>
            {!compact ? (
              <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={toggle} data-testid="connector-toggle">
                <Power className="me-1.5 h-3.5 w-3.5" aria-hidden />
                {status.enabled === false ? tx("turnOn", "Turn on") : tx("turnOff", "Turn off")}
              </Button>
            ) : null}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={busy !== null}
              onClick={() => {
                setReconnect(true);
                setValues(initialValues(method, status));
                setNotice("");
              }}
            >
              {tx("reconnect", "Reconnect")}
            </Button>
            {!compact ? (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy !== null && busy !== "disconnect"}
                onClick={disconnect}
                className="text-destructive hover:text-destructive"
                data-testid="connector-disconnect"
              >
                <Unplug className="me-1.5 h-3.5 w-3.5" aria-hidden />
                {confirmDisconnect ? tx("confirmDisconnect", "Click again to disconnect") : tx("disconnect", "Disconnect")}
              </Button>
            ) : null}
          </div>
        </div>
      ) : (
        <>
          {detail.methods.length > 1 ? (
            <div
              role="tablist"
              aria-label={tx("methods", "How to connect")}
              className={SEGMENT_GROUP_CLASS}
            >
              {detail.methods.map((item) => (
                <button
                  key={item.type}
                  type="button"
                  role="tab"
                  aria-selected={method?.type === item.type}
                  onClick={() => chooseMethod(item)}
                  data-testid={`connector-method-${item.type}`}
                  className={segmentClass(method?.type === item.type)}
                >
                  {methodLabel(item)}
                </button>
              ))}
            </div>
          ) : null}

          {method && method.type !== "oauth2" ? (
            <form onSubmit={submitKey} className="space-y-2.5" data-testid="connector-key-form" autoComplete="off">
              {method.description ? (
                <p className="text-[12px] leading-5 text-muted-foreground" dir="auto">
                  {method.description}
                </p>
              ) : null}
              {fieldInputs(method.fields)}
              <p className="text-[12px] leading-5 text-muted-foreground">
                {tx("keyHint", "Navin checks it with {{name}}, then keeps it in the local vault.", { name: detail.name })}
              </p>
              <Button type="submit" size="sm" disabled={busy !== null} data-testid="connector-connect">
                {busy === "connect" ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Plug className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                {busy === "connect" ? tx("connecting", "Checking with {{name}}...", { name: detail.name }) : tx("connect", "Connect")}
              </Button>
            </form>
          ) : null}

          {method?.type === "oauth2" && oauthApp && needsApp ? (
            <form onSubmit={saveApp} className="space-y-2.5 rounded-xl border border-border/60 bg-muted/30 p-3" data-testid="connector-oauth-app" autoComplete="off">
              <p className="font-medium text-foreground">{tx("appTitle", "{{label}} OAuth app", { label: oauthApp.label })}</p>
              <p className="text-[12px] leading-5 text-muted-foreground">
                {tx(
                  "appHint",
                  "Navin signs in with your own {{label}} OAuth app. Create it, add this redirect URI, then paste its client ID and secret.",
                  { label: oauthApp.label },
                )}
              </p>
              {oauthApp.services.length > 1 ? (
                <p className="text-[12px] text-muted-foreground">
                  {tx("appShared", "Shared by {{count}} connectors.", { count: oauthApp.services.length })}
                </p>
              ) : null}
              <div className="space-y-1">
                <span className="text-[12.5px] font-medium">{tx("redirectUri", "Redirect URI")}</span>
                <div className="flex min-w-0 items-center gap-2">
                  <code dir="ltr" className="min-w-0 flex-1 truncate rounded-lg bg-background px-2 py-1.5 font-mono text-[12px] shadow-[inset_0_0_0_1px_rgba(0,0,0,0.08)]" data-testid="connector-redirect-uri">
                    {app.redirect_uri || oauthApp.suggested_redirect_uri}
                  </code>
                  <Button type="button" size="sm" variant="outline" onClick={copyRedirect} aria-label={tx("copy", "Copy")}>
                    {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
                  </Button>
                </div>
              </div>
              <FieldInput
                field={{ key: "client_id", label: tx("clientId", "Client ID"), inputType: "text", required: true, secret: false }}
                value={app.client_id}
                onChange={(value) => setApp((current) => ({ ...current, client_id: value }))}
                optionalLabel={tx("optional", "optional")}
                idPrefix={`connector-app-${service}`}
              />
              <FieldInput
                field={{
                  key: "client_secret",
                  label: tx("clientSecret", "Client secret"),
                  inputType: "password",
                  required: !oauthApp.has_secret,
                  secret: true,
                  placeholder: oauthApp.has_secret ? tx("secretSaved", "Saved. Leave empty to keep it.") : "",
                }}
                value={app.client_secret}
                onChange={(value) => setApp((current) => ({ ...current, client_secret: value }))}
                optionalLabel={tx("optional", "optional")}
                idPrefix={`connector-app-${service}`}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="submit" size="sm" disabled={busy !== null || !app.client_id.trim()} data-testid="connector-save-app">
                  {busy === "app" ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <Check className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                  {tx("saveApp", "Save the OAuth app")}
                </Button>
                {oauthApp.app_url ? (
                  <Button type="button" size="sm" variant="outline" onClick={() => void openInOsBrowser(apiToken, oauthApp.app_url)}>
                    <ExternalLink className="me-1.5 h-3.5 w-3.5" aria-hidden />
                    {tx("openConsole", "Open the developer console")}
                  </Button>
                ) : null}
                {editApp ? (
                  <Button type="button" size="sm" variant="ghost" onClick={() => setEditApp(false)}>
                    {tx("cancel", "Cancel")}
                  </Button>
                ) : null}
              </div>
            </form>
          ) : null}

          {method?.type === "oauth2" && !needsApp ? (
            <div className="space-y-2.5" data-testid="connector-oauth">
              {fieldInputs(method.fields)}
              {method.scopes && method.scopes.length ? (
                <details className="group rounded-lg text-[12px] text-muted-foreground">
                  <summary className="cursor-pointer select-none">
                    {tx("permissions", "Permissions requested: {{count}}", { count: method.scopes.length })}
                  </summary>
                  <ul className="mt-1.5 max-h-32 space-y-0.5 overflow-y-auto font-mono text-[11.5px]" dir="ltr">
                    {method.scopes.map((scope) => (
                      <li key={scope} className="truncate">
                        {scope}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" size="sm" disabled={busy !== null} onClick={startOAuth} data-testid="connector-oauth-start">
                  {busy === "oauth" || waiting ? <Loader2 className="me-1.5 h-3.5 w-3.5 animate-spin" aria-hidden /> : <LogIn className="me-1.5 h-3.5 w-3.5" aria-hidden />}
                  {tx("signIn", "Sign in with {{name}}", { name: detail.name })}
                </Button>
                <button
                  type="button"
                  className="text-[12px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                  onClick={() => setEditApp(true)}
                >
                  {tx("editApp", "Change the OAuth app")}
                </button>
              </div>
              {waiting ? (
                <p role="status" className="text-[12px] leading-5 text-muted-foreground">
                  {tx("waiting", "Finish signing in in the browser. This form updates by itself.")}
                </p>
              ) : null}
              {authUrl ? (
                <a
                  href={authUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1 text-[12.5px] font-medium text-primary underline-offset-4 hover:underline"
                  data-testid="connector-oauth-link"
                >
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                  {tx("openLink", "Open the sign-in page")}
                </a>
              ) : null}
            </div>
          ) : null}

          {reconnect ? (
            <button
              type="button"
              className="text-[12px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              onClick={() => setReconnect(false)}
            >
              {tx("cancel", "Cancel")}
            </button>
          ) : null}
        </>
      )}

      <AnimatePresence initial={false}>
        {error ? (
          <motion.p
            key="error"
            role="alert"
            initial={reduceMotion ? false : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -4 }}
            transition={spring}
            className="text-[12.5px] leading-5 text-destructive"
            dir="auto"
            data-testid="connector-error"
          >
            {errorFromServer ? (
              <span className="block font-medium">{tx("failed", "That did not work.")}</span>
            ) : null}
            <span className={cn(errorFromServer && "block text-[12px] text-destructive/80")}>{error}</span>
          </motion.p>
        ) : notice ? (
          <motion.p
            key="notice"
            role="status"
            initial={reduceMotion ? false : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={spring}
            className="text-[12.5px] text-emerald-700 dark:text-emerald-300"
          >
            {notice}
          </motion.p>
        ) : null}
      </AnimatePresence>

      <p className="text-[11.5px] leading-5 text-muted-foreground/90">
        {tx("vaultNote", "The agent never sees the key or token: Navin adds it to each call.")}
      </p>
    </div>
  );
}

function FieldInput({
  field,
  value,
  onChange,
  optionalLabel,
  idPrefix,
}: {
  field: ConnectorField;
  value: string;
  onChange: (value: string) => void;
  optionalLabel: string;
  idPrefix: string;
}) {
  const id = `${idPrefix}-${field.key}`;
  const multiline = field.inputType === "textarea";
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-[12.5px] font-medium text-foreground">
        {field.label}
        {!field.required ? <span className="ms-1 font-normal text-muted-foreground">({optionalLabel})</span> : null}
      </label>
      {multiline ? (
        <Textarea
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.placeholder ?? ""}
          rows={4}
          spellCheck={false}
          autoComplete="off"
          dir="ltr"
          className="font-mono text-[12px]"
          data-testid={`connector-field-${field.key}`}
        />
      ) : (
        <Input
          id={id}
          type={field.secret ? "password" : "text"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={field.placeholder ?? ""}
          spellCheck={false}
          autoComplete={field.secret ? "new-password" : "off"}
          dir="ltr"
          className="h-9 rounded-lg text-[13px]"
          data-testid={`connector-field-${field.key}`}
        />
      )}
      {field.description ? (
        <p className="text-[11.5px] leading-5 text-muted-foreground" dir="auto">
          {field.description}
        </p>
      ) : null}
    </div>
  );
}
