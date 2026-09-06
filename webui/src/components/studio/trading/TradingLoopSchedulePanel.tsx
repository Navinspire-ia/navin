import { useEffect, useMemo, useState } from "react";
import {
  ChoiceGroup,
  DefaultButton,
  Dropdown,
  Panel,
  PanelType,
  PrimaryButton,
  TextField,
  Toggle,
  type IChoiceGroupOption,
  type IDropdownOption,
} from "@fluentui/react";

import { LAST_DAY, WEEKDAYS, formatTimeOfDay, weekdayLabel } from "@/lib/loop-schedule";
import type { TradingLoopKind, TradingLoopSchedule } from "@/lib/trading-api";
import {
  DEFAULT_TRADING_SCHEDULE,
  TRADING_LOOP_KINDS,
  describeTradingSchedule,
  formatNextDue,
  normalizeTradingSchedule,
  parseTimeInput,
} from "@/lib/trading-loop-schedule";

const BUTTON_STYLES = {
  root: { minHeight: 40, cursor: "pointer" as const },
};

const CALLOUT = {
  preventDismissOnScroll: true,
  preventDismissOnResize: true,
  isBeakVisible: false,
};

const KIND_COPY: Record<TradingLoopKind, [string, string]> = {
  daily: ["scheduleDaily", "Every day"],
  weekdays: ["scheduleWeekdays", "Weekdays"],
  weekend: ["scheduleWeekend", "Weekend"],
  weekly: ["scheduleWeekly", "Each week"],
  monthly: ["scheduleMonthly", "Each month"],
};

type Tx = (key: string, fallback: string, values?: Record<string, string | number>) => string;

export function TradingLoopSchedulePanel({
  open,
  mode,
  schedule,
  nextDue,
  enabled,
  locale,
  busy,
  tx,
  onDismiss,
  onSubmit,
}: {
  open: boolean;
  mode: "start" | "edit";
  schedule?: TradingLoopSchedule | null;
  nextDue?: number;
  enabled?: boolean;
  locale: string;
  busy?: boolean;
  tx: Tx;
  onDismiss: () => void;
  onSubmit: (schedule: TradingLoopSchedule, runNow: boolean) => void;
}) {
  const [draft, setDraft] = useState<TradingLoopSchedule>(DEFAULT_TRADING_SCHEDULE);
  const [runNow, setRunNow] = useState(false);

  useEffect(() => {
    if (!open) return;
    setDraft(normalizeTradingSchedule(schedule));
    setRunNow(false);
  }, [open, schedule]);

  const summary = describeTradingSchedule(draft, tx, locale);
  const nextLabel =
    enabled && nextDue && nextDue * 1000 > Date.now() ? formatNextDue(nextDue, locale) : "";
  const kindOptions = useMemo<IChoiceGroupOption[]>(
    () =>
      TRADING_LOOP_KINDS.map((kind) => {
        const [key, fallback] = KIND_COPY[kind];
        return { key: kind, text: tx(key, fallback) };
      }),
    [tx],
  );
  const weekdayOptions = useMemo<IDropdownOption[]>(
    () => WEEKDAYS.map((day) => ({ key: day, text: weekdayLabel(day, locale) })),
    [locale],
  );
  const monthDayOptions = useMemo<IDropdownOption[]>(
    () => [
      ...Array.from({ length: 28 }, (_, index) => ({
        key: index + 1,
        text: String(index + 1),
      })),
      { key: LAST_DAY, text: tx("scheduleLastDay", "Last day") },
    ],
    [tx],
  );

  return (
    <Panel
      isOpen={open}
      isLightDismiss
      type={PanelType.medium}
      headerText={
        mode === "start"
          ? tx("scheduleStartTitle", "Start the loop")
          : tx("scheduleEditTitle", "Change the schedule")
      }
      closeButtonAriaLabel={tx("scheduleCancel", "Cancel")}
      onDismiss={onDismiss}
      onRenderFooterContent={() => (
        <div className="flex flex-wrap gap-2">
          <PrimaryButton
            text={
              mode === "start"
                ? tx("scheduleStart", "Start on this schedule")
                : tx("scheduleSave", "Save schedule")
            }
            iconProps={{ iconName: mode === "start" ? "Play" : "Save" }}
            disabled={busy}
            onClick={() => onSubmit(draft, mode === "start" && runNow)}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("scheduleCancel", "Cancel")}
            iconProps={{ iconName: "Cancel" }}
            disabled={busy}
            onClick={onDismiss}
            styles={BUTTON_STYLES}
          />
        </div>
      )}
      isFooterAtBottom
    >
      <p className="text-pretty text-sm text-muted-foreground">
        {tx(
          "scheduleHint",
          "The desk runs by itself at this time while Navin is up. Pause or change it whenever you want.",
        )}
      </p>
      <div className="mt-4 grid gap-4">
        <ChoiceGroup
          label={tx("scheduleCadence", "How often")}
          selectedKey={draft.kind}
          options={kindOptions}
          onChange={(_, option) =>
            setDraft((current) =>
              normalizeTradingSchedule({
                ...current,
                kind: String(option?.key || "daily") as TradingLoopKind,
              }),
            )
          }
        />
        <TextField
          type="time"
          label={tx("scheduleTime", "At this time")}
          value={formatTimeOfDay(draft.hour, draft.minute)}
          onChange={(_, value) => setDraft((current) => parseTimeInput(value || "", current))}
        />
        {draft.kind === "weekly" ? (
          <Dropdown
            label={tx("scheduleWeekday", "Weekday")}
            selectedKey={draft.weekday || 1}
            options={weekdayOptions}
            calloutProps={CALLOUT}
            onChange={(_, option) =>
              setDraft((current) =>
                normalizeTradingSchedule({ ...current, weekday: Number(option?.key || 1) }),
              )
            }
          />
        ) : null}
        {draft.kind === "monthly" ? (
          <Dropdown
            label={tx("scheduleMonthDay", "Day of the month")}
            selectedKey={draft.day || 1}
            options={monthDayOptions}
            calloutProps={CALLOUT}
            onChange={(_, option) =>
              setDraft((current) =>
                normalizeTradingSchedule({
                  ...current,
                  day: option?.key === LAST_DAY ? LAST_DAY : Number(option?.key || 1),
                }),
              )
            }
          />
        ) : null}
        {mode === "start" ? (
          <Toggle
            label={tx("scheduleRunNow", "Also run a cycle now")}
            checked={runNow}
            onText={tx("scheduleRunNowOn", "Yes")}
            offText={tx("scheduleRunNowOff", "No, wait for the next slot")}
            onChange={(_, checked) => setRunNow(Boolean(checked))}
          />
        ) : null}
        <p className="text-pretty text-sm font-medium">{summary}</p>
        {nextLabel && mode === "edit" ? (
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("scheduleNext", "Next run {{time}}", { time: nextLabel })}
          </p>
        ) : null}
      </div>
    </Panel>
  );
}
