// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Settings > Organization (Navin SM): the organization's model gateway.
 *
 * The IT team deploys the organization file (managed settings) or gives it
 * to the user, who imports it here. Navin then uses the organization's
 * LiteLLM proxy and its models; in exclusive mode no other provider can be
 * used. A personal key typed here goes to the gateway settings and never
 * comes back: the screen only shows a masked hint.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { motion, useReducedMotion } from "framer-motion";
import { Building2, CircleAlert, KeyRound, Loader2, Lock, PlugZap, Trash2, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  fetchOrganizationStatus,
  importOrganizationFile,
  removeOrganizationFile,
  saveOrganizationKey,
  testOrganizationGateway,
  type OrganizationStatus,
  type OrganizationTestResult,
} from "@/lib/organization-api";
import { cn } from "@/lib/utils";

type Busy = "import" | "remove" | "key" | "test";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function Group({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-[22px] border border-border/45 bg-card/86 shadow-[0_18px_65px_rgba(15,23,42,0.075)] backdrop-blur-xl dark:border-white/10 dark:shadow-[0_18px_65px_rgba(0,0,0,0.24)]",
        className,
      )}
    >
      <div className="divide-y divide-border/45">{children}</div>
    </div>
  );
}

function Row({ title, description, children }: { title: string; description?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex min-h-[62px] flex-col gap-3 px-4 py-3.5 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-x-9 sm:px-5">
      <div className="min-w-0 sm:flex-[1_1_12rem]">
        <div className="text-[14px] font-medium leading-5 text-foreground">{title}</div>
        {description ? (
          <div className="mt-0.5 max-w-[40rem] break-words text-[12px] leading-5 text-muted-foreground">{description}</div>
        ) : null}
      </div>
      {children ? <div className="min-w-0 max-w-full sm:shrink-0">{children}</div> : null}
    </div>
  );
}

function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium",
        tone === "good"
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : tone === "warn"
            ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
            : "border-border/60 bg-muted/50 text-muted-foreground",
      )}
    >
      {children}
    </span>
  );
}

const BUTTON = "h-9 rounded-full px-4 text-[13px]";

export function OrganizationSettings({ token }: { token: string }) {
  const { t } = useTranslation();
  const reduceMotion = useReducedMotion();
  const fileInput = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<OrganizationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [test, setTest] = useState<OrganizationTestResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setStatus(await fetchOrganizationStatus(token));
    } catch (error) {
      setLoadError(errorText(error));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (kind: Busy, action: () => Promise<OrganizationStatus>, done: string): Promise<boolean> => {
    setBusy(kind);
    setActionError(null);
    setNotice(null);
    try {
      setStatus(await action());
      setTest(null);
      setNotice(done);
      return true;
    } catch (error) {
      setActionError(errorText(error));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    const content = await file.text();
    await run("import", () => importOrganizationFile(token, content), t("settings.organization.imported"));
    if (fileInput.current) fileInput.current.value = "";
  };

  const saveKey = async () => {
    const value = keyDraft.trim();
    if (!value) return;
    if (await run("key", () => saveOrganizationKey(token, value), t("settings.organization.keySaved"))) {
      setKeyDraft("");
    }
  };

  const remove = async () => {
    if (await run("remove", () => removeOrganizationFile(token), t("settings.organization.removed"))) {
      setConfirmRemove(false);
    }
  };

  const runTest = async () => {
    setBusy("test");
    setActionError(null);
    setNotice(null);
    try {
      setTest(await testOrganizationGateway(token));
    } catch (error) {
      setActionError(errorText(error));
    } finally {
      setBusy(null);
    }
  };

  if (!status) {
    if (loading || !loadError) {
      return (
        <div className="flex items-center gap-2 px-1 py-6 text-[13px] text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          {t("settings.organization.loading")}
        </div>
      );
    }
    return (
      <Group>
        <Row title={t("settings.organization.loadFailed")} description={loadError}>
          <Button type="button" variant="outline" className={BUTTON} onClick={() => void load()}>
            {t("settings.organization.retry")}
          </Button>
        </Row>
      </Group>
    );
  }

  const org = status.organization;
  const gateway = status.gateway ?? null;
  const spin = <Loader2 className="me-1.5 h-4 w-4 animate-spin" aria-hidden />;
  const needsKey = gateway?.auth === "user_key" && !status.user_key?.configured;

  return (
    <motion.div
      initial={reduceMotion ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="space-y-5"
    >
      <input
        ref={fileInput}
        type="file"
        accept="application/json,.json"
        className="hidden"
        aria-label={t("settings.organization.chooseFile")}
        onChange={(event) => void onFile(event.target.files?.[0])}
      />
      {!status.present ? (
        <Group>
          <div className="flex flex-col items-start gap-3 px-5 py-6">
            <Building2 className="h-6 w-6 text-muted-foreground" aria-hidden />
            <div>
              <div className="text-[14px] font-medium text-foreground">{t("settings.organization.emptyTitle")}</div>
              <p className="mt-1 max-w-[40rem] text-[12px] leading-5 text-muted-foreground">
                {t("settings.organization.emptyBody")}
              </p>
            </div>
            <Button type="button" className={BUTTON} onClick={() => fileInput.current?.click()} disabled={busy !== null}>
              {busy === "import" ? spin : <Upload className="me-1.5 h-4 w-4" aria-hidden />}
              {t("settings.organization.import")}
            </Button>
          </div>
        </Group>
      ) : (
        <>
          <Group>
            <Row
              title={org?.name || org?.id || t("settings.organization.unnamed")}
              description={
                status.locked
                  ? t("settings.organization.deployedBy", { path: status.path ?? "" })
                  : t("settings.organization.importedFrom", { path: status.path ?? "" })
              }
            >
              <div className="flex flex-wrap items-center gap-2">
                {status.locked ? (
                  <Pill>
                    <Lock className="h-3 w-3" aria-hidden />
                    {t("settings.organization.locked")}
                  </Pill>
                ) : null}
                {status.exclusive ? (
                  <Pill tone="warn">{t("settings.organization.exclusive")}</Pill>
                ) : (
                  <Pill>{t("settings.organization.shared")}</Pill>
                )}
              </div>
            </Row>
            {status.error ? (
              <div role="alert" className="flex items-start gap-2 px-5 py-3.5 text-[12px] leading-5 text-destructive">
                <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                <span>
                  {status.locked && status.exclusive
                    ? t("settings.organization.invalidBlocked", { error: status.error })
                    : t("settings.organization.invalid", { error: status.error })}
                </span>
              </div>
            ) : null}
            {gateway ? (
              <Row title={t("settings.organization.gateway")} description={gateway.base_url}>
                <Button
                  type="button"
                  variant="outline"
                  className={BUTTON}
                  onClick={() => void runTest()}
                  disabled={busy !== null || needsKey}
                >
                  {busy === "test" ? spin : <PlugZap className="me-1.5 h-4 w-4" aria-hidden />}
                  {t("settings.organization.test")}
                </Button>
              </Row>
            ) : null}
            {test ? (
              <div
                role="status"
                className={cn(
                  "flex items-start gap-2 px-5 py-3 text-[12px] leading-5",
                  test.ok ? "text-emerald-700 dark:text-emerald-300" : "text-destructive",
                )}
              >
                {test.ok ? null : <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />}
                <span>
                  {test.ok ? t("settings.organization.testOk", { n: test.served ?? 0 }) : test.error}
                  {test.ok && test.missing && test.missing.length > 0
                    ? ` ${t("settings.organization.testMissing", { models: test.missing.join(", ") })}`
                    : null}
                </span>
              </div>
            ) : null}
            {org?.support_contact ? (
              <Row title={t("settings.organization.support")} description={org.support_contact} />
            ) : null}
          </Group>

          {gateway?.auth === "user_key" ? (
            <Group>
              <Row
                title={t("settings.organization.personalKey")}
                description={
                  status.user_key?.configured
                    ? t("settings.organization.keySet", { hint: status.user_key.hint ?? "" })
                    : t("settings.organization.keyMissing")
                }
              >
                <form
                  className="flex w-full max-w-md items-center gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void saveKey();
                  }}
                >
                  <Input
                    type="password"
                    autoComplete="off"
                    value={keyDraft}
                    onChange={(event) => setKeyDraft(event.target.value)}
                    placeholder={t("settings.organization.keyPlaceholder")}
                    aria-label={t("settings.organization.personalKey")}
                    className="h-9 rounded-full text-[13px]"
                  />
                  <Button type="submit" className={BUTTON} disabled={busy !== null || !keyDraft.trim()}>
                    {busy === "key" ? spin : <KeyRound className="me-1.5 h-4 w-4" aria-hidden />}
                    {t("settings.organization.saveKey")}
                  </Button>
                </form>
              </Row>
            </Group>
          ) : null}

          {gateway ? (
            <div>
              <h2 className="mb-2 px-1 text-[13px] font-semibold tracking-[-0.01em] text-foreground/85">
                {t("settings.organization.modelsTitle", { n: gateway.models.length })}
              </h2>
              <Group>
                {gateway.models.map((model) => (
                  <Row key={model.id} title={model.label} description={model.id}>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {model.default ? <Pill>{t("settings.organization.default")}</Pill> : null}
                      {model.active ? <Pill tone="good">{t("settings.organization.inUse")}</Pill> : null}
                      {model.vision ? <Pill>{t("settings.organization.vision")}</Pill> : null}
                      {model.context_window ? (
                        <Pill>{t("settings.organization.window", { tokens: model.context_window.toLocaleString() })}</Pill>
                      ) : null}
                    </div>
                  </Row>
                ))}
              </Group>
            </div>
          ) : null}

          {!status.locked ? (
            <Group>
              <Row title={t("settings.organization.fileTitle")} description={t("settings.organization.fileBody")}>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    className={BUTTON}
                    onClick={() => fileInput.current?.click()}
                    disabled={busy !== null}
                  >
                    {busy === "import" ? spin : <Upload className="me-1.5 h-4 w-4" aria-hidden />}
                    {t("settings.organization.replace")}
                  </Button>
                  {confirmRemove ? (
                    <>
                      <Button
                        type="button"
                        variant="destructive"
                        className={BUTTON}
                        onClick={() => void remove()}
                        disabled={busy !== null}
                      >
                        {busy === "remove" ? spin : <Trash2 className="me-1.5 h-4 w-4" aria-hidden />}
                        {t("settings.organization.confirmRemove")}
                      </Button>
                      <Button type="button" variant="ghost" className={BUTTON} onClick={() => setConfirmRemove(false)}>
                        {t("settings.organization.cancel")}
                      </Button>
                    </>
                  ) : (
                    <Button
                      type="button"
                      variant="ghost"
                      className={BUTTON}
                      onClick={() => setConfirmRemove(true)}
                      disabled={busy !== null}
                    >
                      <Trash2 className="me-1.5 h-4 w-4" aria-hidden />
                      {t("settings.organization.remove")}
                    </Button>
                  )}
                </div>
              </Row>
            </Group>
          ) : null}
        </>
      )}
      {actionError ? (
        <p role="alert" className="px-1 text-[12px] leading-5 text-destructive">
          {actionError}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="px-1 text-[12px] leading-5 text-muted-foreground">
          {notice}
        </p>
      ) : null}
    </motion.div>
  );
}
