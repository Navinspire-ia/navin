import { BookMarked, Loader2, Plus, Save } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetchProjectRules, saveProjectRule, type ProjectRuleEntry } from "@/lib/api";
import { notifyProjectRulesChanged } from "@/lib/rail-counts";
import { cn } from "@/lib/utils";

/**
 * Editable ``.navin/rules/*.md`` for the bound project (Cursor-rules equivalent).
 */
export function DevRulesPanel({
  token,
  sessionKey,
}: {
  token: string;
  sessionKey: string;
}) {
  const { t } = useTranslation();
  const [rules, setRules] = useState<ProjectRuleEntry[]>([]);
  const [rulesDir, setRulesDir] = useState(".navin/rules");
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    if (!token || !sessionKey) {
      setRules([]);
      return;
    }
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchProjectRules(token, sessionKey);
      if (id !== requestId.current) return;
      setRules(payload.rules ?? []);
      setRulesDir(payload.rules_dir || ".navin/rules");
      setSelected((prev) => {
        if (prev && payload.rules.some((rule) => rule.name === prev)) return prev;
        return payload.rules[0]?.name ?? null;
      });
    } catch (err) {
      if (id !== requestId.current) return;
      setError(err instanceof Error ? err.message : "failed to load rules");
      setRules([]);
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [sessionKey, token]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const rule = rules.find((row) => row.name === selected) ?? null;
    setDraft(rule?.content ?? "");
    setDirty(false);
  }, [rules, selected]);

  const onSave = async () => {
    if (!selected) return;
    setSaving(true);
    setError(null);
    try {
      const payload = await saveProjectRule(token, sessionKey, selected, draft);
      setRules((prev) => {
        const next = prev.filter((row) => row.name !== payload.rule.name);
        next.push(payload.rule);
        next.sort((a, b) => a.name.localeCompare(b.name));
        return next;
      });
      setDirty(false);
      notifyProjectRulesChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "save failed");
    } finally {
      setSaving(false);
    }
  };

  const onCreate = async () => {
    const name = newName.trim().replace(/\.md$/i, "");
    if (!name) return;
    setSaving(true);
    setError(null);
    try {
      const payload = await saveProjectRule(token, sessionKey, name, "");
      setRules((prev) => {
        const next = prev.filter((row) => row.name !== payload.rule.name);
        next.push(payload.rule);
        next.sort((a, b) => a.name.localeCompare(b.name));
        return next;
      });
      notifyProjectRulesChanged();
      setSelected(payload.rule.name);
      setCreating(false);
      setNewName("");
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "create failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1.5 border-b border-border/40 px-2 py-1.5">
        <BookMarked className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-muted-foreground">
          {rulesDir}
        </span>
        <button
          type="button"
          onClick={() => setCreating((open) => !open)}
          className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          title={t("dev.rules.new", { defaultValue: "New rule" })}
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      {creating ? (
        <div className="flex shrink-0 items-center gap-1 border-b border-border/40 px-2 py-1.5">
          <Input
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder={t("dev.rules.namePlaceholder", {
              defaultValue: "architecture",
            })}
            className="h-7 flex-1 text-[12px]"
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void onCreate();
              }
            }}
          />
          <Button
            type="button"
            size="sm"
            className="h-7 px-2 text-[11px]"
            disabled={saving || !newName.trim()}
            onClick={() => void onCreate()}
          >
            {t("dev.rules.create", { defaultValue: "Create" })}
          </Button>
        </div>
      ) : null}

      {error ? (
        <p className="shrink-0 px-2 py-1.5 text-[11px] text-red-500">{error}</p>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <ul className="w-[7.5rem] shrink-0 overflow-y-auto border-r border-border/40 py-1">
          {loading && rules.length === 0 ? (
            <li className="flex items-center gap-1 px-2 py-2 text-[11px] text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              {t("dev.rules.loading", { defaultValue: "Loading..." })}
            </li>
          ) : rules.length === 0 ? (
            <li className="px-2 py-2 text-[11px] text-muted-foreground">
              {t("dev.rules.empty", {
                defaultValue: "No rules yet. Create one for this project.",
              })}
            </li>
          ) : (
            rules.map((rule) => (
              <li key={rule.name}>
                <button
                  type="button"
                  onClick={() => setSelected(rule.name)}
                  className={cn(
                    "block w-full truncate px-2 py-1 text-left text-[11px]",
                    selected === rule.name
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                  )}
                  title={rule.path}
                >
                  {rule.name}
                </button>
              </li>
            ))
          )}
        </ul>

        <div className="flex min-w-0 flex-1 flex-col">
          {selected ? (
            <>
              <div className="flex shrink-0 items-center gap-1 border-b border-border/40 px-2 py-1">
                <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-muted-foreground">
                  {rules.find((row) => row.name === selected)?.path ?? selected}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant={dirty ? "default" : "ghost"}
                  className="h-7 gap-1 px-2 text-[11px]"
                  disabled={!dirty || saving}
                  onClick={() => void onSave()}
                >
                  {saving ? (
                    <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                  ) : (
                    <Save className="h-3 w-3" aria-hidden />
                  )}
                  {t("dev.rules.save", { defaultValue: "Save" })}
                </Button>
              </div>
              <textarea
                value={draft}
                onChange={(event) => {
                  setDraft(event.target.value);
                  setDirty(true);
                }}
                spellCheck={false}
                className="min-h-0 flex-1 resize-none bg-transparent px-2 py-1.5 font-mono text-[11.5px] leading-relaxed text-foreground outline-none"
                aria-label={t("dev.rules.editor", { defaultValue: "Rule content" })}
              />
            </>
          ) : (
            <p className="px-3 py-4 text-[12px] text-muted-foreground">
              {t("dev.rules.pick", {
                defaultValue: "Select or create a project rule.",
              })}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export default DevRulesPanel;
