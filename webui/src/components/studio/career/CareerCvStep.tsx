import { DefaultButton, PrimaryButton, TextField } from "@fluentui/react";
import "@/lib/fluent-icons";

import { BUTTON_STYLES, type Tx } from "@/components/studio/career/career-ui";
import type { CareerEducation, CareerExperience } from "@/lib/career-api";
import { cn } from "@/lib/utils";

export function composeMasterCv(input: {
  name: string;
  headline: string;
  experiences: CareerExperience[];
  education: CareerEducation[];
  strengths: string[];
}): string {
  const lines: string[] = [];
  if (input.name.trim()) lines.push(input.name.trim());
  if (input.headline.trim()) lines.push(input.headline.trim(), "");
  const jobs = input.experiences.filter((row) => row.title || row.company || row.facts);
  if (jobs.length) {
    lines.push("Experience");
    for (const row of jobs) {
      const head = [row.title, row.company].filter(Boolean).join(" - ");
      const when = row.period ? ` (${row.period})` : "";
      if (head || when) lines.push(`${head}${when}`.trim());
      if (row.facts?.trim()) lines.push(row.facts.trim());
      lines.push("");
    }
  }
  const schools = input.education.filter((row) => row.school || row.diploma);
  if (schools.length) {
    lines.push("Education");
    for (const row of schools) {
      const head = [row.diploma, row.school].filter(Boolean).join(" - ");
      const when = row.year ? ` (${row.year})` : "";
      lines.push(`${head}${when}`.trim());
    }
    lines.push("");
  }
  if (input.strengths.length) {
    lines.push("Strengths");
    lines.push(input.strengths.join(" · "));
  }
  return lines.join("\n").trim();
}

function emptyExperience(): CareerExperience {
  return { title: "", company: "", period: "", facts: "" };
}

function emptyEducation(): CareerEducation {
  return { school: "", diploma: "", year: "" };
}

export function CareerCvPanel({
  tx,
  kind,
  displayName,
  setDisplayName,
  headline,
  setHeadline,
  masterCv,
  setMasterCv,
  experiences,
  setExperiences,
  education,
  setEducation,
  strengths,
  cvPath,
  setCvPath,
  onPolish,
  onCompose,
}: {
  tx: Tx;
  kind: "solo" | "company";
  displayName: string;
  setDisplayName: (value: string) => void;
  headline: string;
  setHeadline: (value: string) => void;
  masterCv: string;
  setMasterCv: (value: string) => void;
  experiences: CareerExperience[];
  setExperiences: (value: CareerExperience[]) => void;
  education: CareerEducation[];
  setEducation: (value: CareerEducation[]) => void;
  strengths: string[];
  cvPath: "" | "import" | "create";
  setCvPath: (value: "import" | "create") => void;
  onPolish: () => void;
  onCompose: () => void;
}) {
  return (
    <div className="grid gap-6">
      <TextField
        label={kind === "company" ? tx("wizard.talentName", "Talent name") : tx("wizard.yourName", "Your name")}
        value={displayName}
        onChange={(_, value) => setDisplayName(value || "")}
      />
      <TextField
        label={tx("wizard.headline", "Headline")}
        value={headline}
        onChange={(_, value) => setHeadline(value || "")}
        placeholder={tx("wizard.headlinePh", "Senior Data Engineer - Spark, Databricks, AWS")}
      />

      <div className="grid gap-3">
        <p className="text-pretty text-sm text-muted-foreground">
          {tx("wizard.cvPathHint", "Paste an existing CV, or build one fact by fact. The agent layouts it. It never invents.")}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <button
            type="button"
            onClick={() => setCvPath("import")}
            className={cn(
              "min-h-24 rounded-2xl px-5 py-4 text-left outline outline-1",
              cvPath === "import"
                ? "bg-indigo-600/10 outline-indigo-500"
                : "outline-black/10 hover:bg-muted/40 dark:outline-white/10",
            )}
          >
            <p className="text-base font-semibold">{tx("wizard.cvImport", "I already have a CV")}</p>
            <p className="mt-2 text-pretty text-sm text-muted-foreground">
              {tx("wizard.cvImportHint", "Paste or import the text. Then ask the agent to layout it.")}
            </p>
          </button>
          <button
            type="button"
            onClick={() => setCvPath("create")}
            className={cn(
              "min-h-24 rounded-2xl px-5 py-4 text-left outline outline-1",
              cvPath === "create"
                ? "bg-indigo-600/10 outline-indigo-500"
                : "outline-black/10 hover:bg-muted/40 dark:outline-white/10",
            )}
          >
            <p className="text-base font-semibold">{tx("wizard.cvCreate", "Create a CV")}</p>
            <p className="mt-2 text-pretty text-sm text-muted-foreground">
              {tx("wizard.cvCreateHint", "Missions, results, education. Real facts only.")}
            </p>
          </button>
        </div>
      </div>

      {cvPath === "import" ? (
        <div className="grid gap-4">
          <TextField
            label={tx("masterCv", "Master CV (facts only)")}
            multiline
            rows={12}
            value={masterCv}
            onChange={(_, value) => setMasterCv(value || "")}
            placeholder={tx("masterCvHint", "Paste real experience only. Navin reorders, it does not invent.")}
          />
          <div className="flex flex-wrap gap-3">
            <DefaultButton
              text={tx("wizard.uploadCv", "Import a text CV")}
              iconProps={{ iconName: "Upload" }}
              onClick={() => {
                const input = document.createElement("input");
                input.type = "file";
                input.accept = ".txt,.md,.text";
                input.onchange = () => {
                  const file = input.files?.[0];
                  if (!file) return;
                  void file.text().then((text) => setMasterCv(text));
                };
                input.click();
              }}
              styles={BUTTON_STYLES}
            />
            <PrimaryButton
              text={tx("wizard.polishCv", "Ask the agent to layout the CV")}
              iconProps={{ iconName: "Edit" }}
              disabled={!masterCv.trim()}
              onClick={onPolish}
              styles={BUTTON_STYLES}
            />
          </div>
        </div>
      ) : null}

      {cvPath === "create" ? (
        <div className="grid gap-5">
          <div className="grid gap-4">
            <p className="text-sm font-semibold">{tx("wizard.experienceTitle", "Experience")}</p>
            {(experiences.length ? experiences : [emptyExperience()]).map((row, index) => (
              <div key={`exp-${index}`} className="grid gap-3 rounded-2xl bg-muted/30 p-4">
                <TextField
                  label={tx("wizard.expTitle", "Role")}
                  value={row.title || ""}
                  onChange={(_, value) => {
                    const next = [...(experiences.length ? experiences : [emptyExperience()])];
                    next[index] = { ...row, title: value || "" };
                    setExperiences(next);
                  }}
                />
                <TextField
                  label={tx("wizard.expCompany", "Company")}
                  value={row.company || ""}
                  onChange={(_, value) => {
                    const next = [...(experiences.length ? experiences : [emptyExperience()])];
                    next[index] = { ...row, company: value || "" };
                    setExperiences(next);
                  }}
                />
                <TextField
                  label={tx("wizard.expPeriod", "Period")}
                  value={row.period || ""}
                  onChange={(_, value) => {
                    const next = [...(experiences.length ? experiences : [emptyExperience()])];
                    next[index] = { ...row, period: value || "" };
                    setExperiences(next);
                  }}
                  placeholder="2022 - 2026"
                />
                <TextField
                  label={tx("wizard.expFacts", "What you actually did")}
                  multiline
                  rows={3}
                  value={row.facts || ""}
                  onChange={(_, value) => {
                    const next = [...(experiences.length ? experiences : [emptyExperience()])];
                    next[index] = { ...row, facts: value || "" };
                    setExperiences(next);
                  }}
                />
              </div>
            ))}
            <DefaultButton
              text={tx("wizard.addExperience", "Add an experience")}
              iconProps={{ iconName: "Add" }}
              onClick={() => setExperiences([...(experiences.length ? experiences : [emptyExperience()]), emptyExperience()])}
              styles={BUTTON_STYLES}
            />
          </div>
          <div className="grid gap-4">
            <p className="text-sm font-semibold">{tx("wizard.educationTitle", "Education")}</p>
            {(education.length ? education : [emptyEducation()]).map((row, index) => (
              <div key={`edu-${index}`} className="grid gap-3 rounded-2xl bg-muted/30 p-4">
                <TextField
                  label={tx("wizard.eduDiploma", "Diploma")}
                  value={row.diploma || ""}
                  onChange={(_, value) => {
                    const next = [...(education.length ? education : [emptyEducation()])];
                    next[index] = { ...row, diploma: value || "" };
                    setEducation(next);
                  }}
                />
                <TextField
                  label={tx("wizard.eduSchool", "School")}
                  value={row.school || ""}
                  onChange={(_, value) => {
                    const next = [...(education.length ? education : [emptyEducation()])];
                    next[index] = { ...row, school: value || "" };
                    setEducation(next);
                  }}
                />
                <TextField
                  label={tx("wizard.eduYear", "Year")}
                  value={row.year || ""}
                  onChange={(_, value) => {
                    const next = [...(education.length ? education : [emptyEducation()])];
                    next[index] = { ...row, year: value || "" };
                    setEducation(next);
                  }}
                />
              </div>
            ))}
            <DefaultButton
              text={tx("wizard.addEducation", "Add a diploma")}
              onClick={() => setEducation([...(education.length ? education : [emptyEducation()]), emptyEducation()])}
              styles={BUTTON_STYLES}
            />
          </div>
          <div className="flex flex-wrap gap-3">
            <PrimaryButton
              text={tx("wizard.composeCv", "Build the CV from these facts")}
              onClick={() => {
                setMasterCv(
                  composeMasterCv({
                    name: displayName,
                    headline,
                    experiences,
                    education,
                    strengths,
                  }),
                );
                onCompose();
              }}
              styles={BUTTON_STYLES}
            />
            <DefaultButton
              text={tx("wizard.polishCv", "Ask the agent to layout the CV")}
              disabled={!masterCv.trim()}
              onClick={onPolish}
              styles={BUTTON_STYLES}
            />
          </div>
          {masterCv ? (
            <TextField
              label={tx("wizard.cvPreview", "CV draft (facts only)")}
              multiline
              rows={8}
              value={masterCv}
              onChange={(_, value) => setMasterCv(value || "")}
            />
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function CareerDossierPanel({
  tx,
  masterCv,
  strengths,
  setStrengths,
  weaknesses,
  setWeaknesses,
  strengthDraft,
  setStrengthDraft,
  weaknessDraft,
  setWeaknessDraft,
  onAdd,
  onExtract,
  onRewrite,
}: {
  tx: Tx;
  masterCv: string;
  strengths: string[];
  setStrengths: (value: string[]) => void;
  weaknesses: string[];
  setWeaknesses: (value: string[]) => void;
  strengthDraft: string;
  setStrengthDraft: (value: string) => void;
  weaknessDraft: string;
  setWeaknessDraft: (value: string) => void;
  onAdd: (value: string, list: string[], setList: (next: string[]) => void, setDraft: (next: string) => void) => void;
  onExtract: (kind: "strengths" | "weaknesses") => void;
  onRewrite: (kind: "strengths" | "weaknesses") => void;
}) {
  return (
    <div className="grid gap-6">
      <p className="text-pretty text-sm text-muted-foreground">
        {tx(
          "wizard.dossierHint",
          "This is the competence dossier. Extract, add, rewrite. Keep it honest. The agent never invents a skill.",
        )}
      </p>
      <section className="grid gap-3">
        <TextField
          label={tx("wizard.strengths", "Strengths to highlight")}
          value={strengthDraft}
          placeholder={tx("addThenEnter", "Type, then Enter")}
          onChange={(_, value) => setStrengthDraft(value || "")}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onAdd(strengthDraft, strengths, setStrengths, setStrengthDraft);
            }
          }}
        />
        <TokenRow values={strengths} onRemove={(value) => setStrengths(strengths.filter((row) => row !== value))} />
        <div className="flex flex-wrap gap-3">
          <DefaultButton
            text={tx("wizard.extractStrengths", "Extract from the CV")}
            disabled={!masterCv.trim()}
            onClick={() => onExtract("strengths")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("wizard.rewriteStrengths", "Rewrite and highlight")}
            disabled={!strengths.length && !masterCv.trim()}
            onClick={() => onRewrite("strengths")}
            styles={BUTTON_STYLES}
          />
        </div>
      </section>
      <section className="grid gap-3">
        <TextField
          label={tx("wizard.weaknesses", "Gaps to watch (honest)")}
          value={weaknessDraft}
          placeholder={tx("addThenEnter", "Type, then Enter")}
          onChange={(_, value) => setWeaknessDraft(value || "")}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              onAdd(weaknessDraft, weaknesses, setWeaknesses, setWeaknessDraft);
            }
          }}
        />
        <TokenRow values={weaknesses} onRemove={(value) => setWeaknesses(weaknesses.filter((row) => row !== value))} />
        <div className="flex flex-wrap gap-3">
          <DefaultButton
            text={tx("wizard.extractWeaknesses", "Extract gaps from the CV")}
            disabled={!masterCv.trim()}
            onClick={() => onExtract("weaknesses")}
            styles={BUTTON_STYLES}
          />
          <DefaultButton
            text={tx("wizard.rewriteWeaknesses", "Rewrite honestly")}
            disabled={!weaknesses.length && !masterCv.trim()}
            onClick={() => onRewrite("weaknesses")}
            styles={BUTTON_STYLES}
          />
        </div>
      </section>
    </div>
  );
}

function TokenRow({ values, onRemove }: { values: string[]; onRemove: (value: string) => void }) {
  if (!values.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {values.map((value) => (
        <button
          key={value}
          type="button"
          onClick={() => onRemove(value)}
          className="rounded-full bg-muted px-3 py-1.5 text-xs font-medium text-foreground"
        >
          {value} ×
        </button>
      ))}
    </div>
  );
}
