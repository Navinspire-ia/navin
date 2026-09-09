// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { DefaultButton, PrimaryButton, TextField } from "@fluentui/react";
import "@/lib/fluent-icons";
import { useTranslation } from "react-i18next";

import { CareerCountryMultiSelect } from "@/components/studio/career/CareerCountrySelect";
import { CareerScene } from "@/components/studio/career/CareerScene";
import { BUTTON_STYLES, DEFAULT_SEARCH_COUNTRIES, SPRING, SURFACE, type Tx } from "@/components/studio/career/career-ui";
import type { CareerProfile, CareerTrack } from "@/lib/career-api";
import { BILLABLE_DAYS, formatMoney, monthlyFromSalary } from "@/lib/career-money";
import { cn } from "@/lib/utils";

/** Steps drawn on the arrival screen. They mirror what the desk actually does. */
const MECHANIC = [
  { id: "find", icon: "Search" },
  { id: "prepare", icon: "TextDocument" },
  { id: "apply", icon: "Send" },
] as const;

export function CareerStart({
  tx,
  track,
  profile,
  busy,
  onTrack,
  onLaunch,
  onDetails,
}: {
  tx: Tx;
  track: CareerTrack;
  profile: CareerProfile;
  busy: boolean;
  onTrack: (track: CareerTrack) => void;
  onLaunch: (body: Record<string, unknown>) => void;
  onDetails: () => void;
}) {
  const { t, i18n } = useTranslation();
  const reduceMotion = useReducedMotion();
  const lang = (i18n.language || "fr").slice(0, 2);
  const isFreelance = track === "freelance";

  const [job, setJob] = useState((profile.titles || []).join(", "));
  const [rate, setRate] = useState(String(profile.min_rate || ""));
  const [salary, setSalary] = useState(String(profile.min_salary || ""));
  const [countries, setCountries] = useState<string[]>(
    (profile.countries_primary || []).length
      ? profile.countries_primary || []
      : [...DEFAULT_SEARCH_COUNTRIES],
  );

  const goal = Number((isFreelance ? rate : salary).replace(/[^\d.]/g, "")) || 0;
  const monthly = isFreelance ? goal * BILLABLE_DAYS : monthlyFromSalary(goal);
  const currency = String(profile.currency || "EUR");
  const monthlyLabel = formatMoney(monthly, currency, lang);
  const yearlyLabel = formatMoney(monthly * 12, currency, lang);
  const ready = Boolean(job.trim() && goal > 0 && countries.length);

  const launch = () =>
    onLaunch({
      titles: job.split(",").map((item) => item.trim()).filter(Boolean),
      countries_primary: countries,
      track,
      engagement: isFreelance ? "freelance" : "permanent",
      ...(isFreelance ? { min_rate: goal } : { min_salary: goal }),
      wizard_complete: true,
      wizard_step: 1,
    });

  return (
    <div className="mx-auto grid w-full max-w-4xl gap-6" data-testid="career-start">
      <motion.div
        initial={reduceMotion ? false : { opacity: 0, y: 12, filter: "blur(4px)" }}
        animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
        transition={SPRING}
        className={cn(SURFACE, "grid gap-6 p-6 sm:p-8")}
      >
        <div className="grid gap-6 sm:grid-cols-[1fr_auto] sm:items-center">
          <div className="min-w-0">
            <p className="text-[12px] font-medium uppercase tracking-[0.14em] text-indigo-700 dark:text-indigo-300">
              {tx("start.kicker", "Get paid")}
            </p>
            <h2 className="mt-2 text-balance text-3xl font-semibold tracking-tight">
              {isFreelance
                ? tx("start.titleFreelance", "How much do you want to earn?")
                : tx("start.titleJobs", "What salary are you going for?")}
            </h2>
            <p className="mt-3 max-w-lg text-pretty text-sm leading-relaxed text-muted-foreground">
              {tx(
                "start.sub",
                "Three answers and Navin starts hunting. Everything else can wait.",
              )}
            </p>
          </div>
          <div className="w-full sm:w-52">
            <CareerScene
              matchRatio={ready ? 0.85 : 0.25}
              active={!busy}
              label={tx("sceneAria", "Three dimensional briefcase for the career pipeline")}
            />
          </div>
        </div>

        <div
          className="flex flex-wrap gap-2"
          role="tablist"
          aria-label={tx("tracksAria", "Career tracks")}
        >
          {(["freelance", "jobs"] as const).map((id) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={track === id}
              onClick={() => onTrack(id)}
              className={cn(
                "min-h-10 cursor-pointer rounded-full px-4 text-sm font-medium outline outline-1 transition-[background-color,color] duration-150",
                track === id
                  ? "bg-indigo-600 text-white outline-indigo-600"
                  : "bg-background text-foreground outline-black/10 hover:bg-muted/60 dark:outline-white/10",
              )}
            >
              {id === "freelance" ? tx("trackFreelance", "Freelance") : tx("trackJobs", "Jobs")}
            </button>
          ))}
        </div>

        <div className="grid gap-5">
          <TextField
            label={tx("start.job", "Your job")}
            value={job}
            onChange={(_, value) => setJob(value || "")}
            placeholder={tx("start.jobPh", "Data Engineer, AI Engineer")}
          />
          {isFreelance ? (
            <TextField
              label={tx("start.rate", "Your daily rate")}
              value={rate}
              onChange={(_, value) => setRate(value || "")}
              suffix={`${currency} / ${tx("start.day", "day")}`}
              placeholder="650"
            />
          ) : (
            <TextField
              label={tx("start.salary", "Salary you want")}
              value={salary}
              onChange={(_, value) => setSalary(value || "")}
              suffix={currency}
              placeholder="60000"
            />
          )}
          <div>
            <p className="mb-2 text-[12px] font-medium text-foreground">
              {tx("start.where", "Where you want to work")}
            </p>
            <p className="mb-3 text-pretty text-sm text-muted-foreground">
              {tx("start.whereHint", "Search the full country list and add as many as you want.")}
            </p>
            <CareerCountryMultiSelect
              values={countries}
              onChange={setCountries}
              lang={lang}
              placeholder={tx("wizard.addCountry", "Add a country")}
              searchPlaceholder={tx("wizard.addCountry", "Add a country")}
              emptyLabel={t("crm.noResults", { defaultValue: "No results" })}
              testId="career-start-countries"
            />
          </div>
        </div>

        {monthly > 0 ? (
          <motion.div
            initial={reduceMotion ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={SPRING}
            className="rounded-xl bg-indigo-500/10 px-5 py-4"
            data-testid="career-start-money"
          >
            <p className="text-[12px] font-medium text-muted-foreground">
              {isFreelance
                ? tx("start.mathFreelance", "At that rate, {{days}} billed days a month", {
                    days: BILLABLE_DAYS,
                  })
                : tx("start.mathJobs", "That salary, month by month")}
            </p>
            <p className="mt-1 text-3xl font-semibold tabular-nums tracking-tight">
              {monthlyLabel}
              <span className="ml-1 text-base font-normal text-muted-foreground">
                {tx("start.perMonth", "/ month")}
              </span>
            </p>
            {yearlyLabel ? (
              <p className="mt-1 text-sm tabular-nums text-muted-foreground">
                {tx("start.perYear", "{{amount}} a year", { amount: yearlyLabel })}
              </p>
            ) : null}
          </motion.div>
        ) : null}

        <div className="flex flex-wrap items-center gap-3">
          <PrimaryButton
            styles={BUTTON_STYLES}
            iconProps={{ iconName: "Search" }}
            disabled={!ready || busy}
            text={
              isFreelance
                ? tx("start.launchFreelance", "Find me missions")
                : tx("start.launchJobs", "Find me jobs")
            }
            onClick={launch}
          />
          <DefaultButton
            styles={BUTTON_STYLES}
            iconProps={{ iconName: "Settings" }}
            text={tx("start.details", "Add my CV and details")}
            onClick={onDetails}
          />
        </div>
        {!ready ? (
          <p className="text-pretty text-sm text-muted-foreground">
            {tx("start.needed", "Fill the three fields above to start the search.")}
          </p>
        ) : null}
      </motion.div>

      <motion.ol
        initial={reduceMotion ? false : { opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...SPRING, delay: 0.1 }}
        className="grid gap-3 sm:grid-cols-3"
      >
        {MECHANIC.map((step, index) => (
          <li key={step.id} className={cn(SURFACE, "p-5")}>
            <p className="text-[12px] font-medium tabular-nums text-indigo-700 dark:text-indigo-300">
              {index + 1}
            </p>
            <p className="mt-1 text-base font-semibold">{tx(`start.${step.id}`, step.id)}</p>
            <p className="mt-1.5 text-pretty text-sm leading-relaxed text-muted-foreground">
              {tx(`start.${step.id}Hint`, step.id)}
            </p>
          </li>
        ))}
      </motion.ol>
    </div>
  );
}
