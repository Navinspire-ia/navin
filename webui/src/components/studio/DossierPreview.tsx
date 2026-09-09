// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

type Block =
  | { kind: "table"; rows: string[][] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "p"; text: string };

function parseBlocks(body: string): Block[] {
  const lines = body.replace(/\u2014/g, " - ").replace(/\u2013/g, "-").split(/\r?\n/);
  const blocks: Block[] = [];
  let table: string[][] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flushTable = () => {
    if (table.length >= 2) blocks.push({ kind: "table", rows: table });
    table = [];
  };
  const flushList = () => {
    if (list?.items.length) blocks.push({ kind: "list", ordered: list.ordered, items: list.items });
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushTable();
      flushList();
      continue;
    }
    if (line.startsWith("|") && !line.includes("---")) {
      flushList();
      table.push(line.replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim()));
      continue;
    }
    if (line.includes("---") && line.startsWith("|")) {
      continue;
    }
    flushTable();
    const bullet = line.match(/^[-*]\s+(.+)/);
    const numbered = line.match(/^\d+[.)]\s+(.+)/);
    if (bullet || numbered) {
      const ordered = Boolean(numbered);
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((bullet?.[1] || numbered?.[1] || "").trim());
      continue;
    }
    flushList();
    blocks.push({ kind: "p", text: line });
  }
  flushTable();
  flushList();
  return blocks;
}

export function DossierPreview({ title, body }: { title: string; body?: string }) {
  if (!body?.trim()) return null;
  const blocks = parseBlocks(body);
  return (
    <section className="rounded-xl border border-[#1B365D]/15 bg-muted/30 p-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{title}</p>
      <div className="mt-3 space-y-2 text-[13px] leading-relaxed text-pretty">
        {blocks.map((block, index) => {
          if (block.kind === "table") {
            return (
              <div key={`t-${index}`} className="overflow-x-auto">
                <table className="w-full border-collapse text-left text-[12px] tabular-nums">
                  <thead>
                    <tr className="bg-[#1B365D] text-white">
                      {block.rows[0].map((cell, cellIndex) => (
                        <th key={cellIndex} className="px-2 py-1.5 font-semibold">
                          {cell}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.slice(1).map((row, rowIndex) => (
                      <tr key={rowIndex} className={rowIndex % 2 ? "bg-[#F4F7FA] dark:bg-white/5" : ""}>
                        {row.map((cell, cellIndex) => (
                          <td key={cellIndex} className="px-2 py-1.5 align-top">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          }
          if (block.kind === "list") {
            const List = block.ordered ? "ol" : "ul";
            return (
              <List key={`l-${index}`} className={block.ordered ? "list-decimal pl-5" : "list-disc pl-5"}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{item}</li>
                ))}
              </List>
            );
          }
          return <p key={`p-${index}`}>{block.text}</p>;
        })}
      </div>
    </section>
  );
}

export function CvPreview({
  name,
  headline,
  target,
  contacts,
  summary,
  skills,
  strengths,
  highlights,
  sections,
  experiences,
  education,
  languages,
  cover,
  labels,
}: {
  name?: string;
  headline?: string;
  target?: string;
  contacts?: string[];
  summary?: string;
  skills?: string;
  strengths?: string[];
  highlights?: string[];
  sections?: { kind?: string; heading: string; paragraphs: string[] }[];
  experiences?: { title?: string; company?: string; period?: string; bullets?: string[] }[];
  education?: { diploma?: string; school?: string; year?: string }[];
  languages?: string[];
  cover?: string;
  labels: {
    profile: string;
    skills: string;
    strengths: string;
    highlights: string;
    experience: string;
    education: string;
    languages: string;
    letter: string;
  };
}) {
  return (
    <article className="rounded-xl border border-[#1B365D]/15 bg-muted/30 p-5">
      <header className="border-b-2 border-[#1B365D] pb-3 text-center">
        {name ? <p className="text-xl font-semibold tracking-tight text-[#1B365D] dark:text-[#9DB4D0]">{name}</p> : null}
        {headline ? <p className="mt-1 text-sm text-pretty">{headline}</p> : null}
        {target ? <p className="mt-1 text-[12px] font-semibold text-[#1B365D] dark:text-[#9DB4D0]">{target}</p> : null}
        {contacts?.length ? <p className="mt-2 text-[12px] text-muted-foreground">{contacts.join("  ·  ")}</p> : null}
      </header>
      {summary ? (
        <section className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.profile}</p>
          <p className="mt-1 text-[13px] leading-relaxed text-pretty">{summary}</p>
        </section>
      ) : null}
      {skills ? (
        <section className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.skills}</p>
          <p className="mt-1 text-[13px] leading-relaxed">{skills}</p>
        </section>
      ) : null}
      {strengths?.length ? (
        <section className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.strengths}</p>
          <ul className="mt-1 list-disc pl-5 text-[13px]">
            {strengths.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {experiences?.length ? (
        <section className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.experience}</p>
          <div className="mt-2 space-y-3">
            {experiences.map((row, index) => (
              <div key={`${row.title}-${index}`}>
                <p className="text-[13px] font-semibold">
                  {[row.title, row.company].filter(Boolean).join("  ·  ")}
                  {row.period ? <span className="ml-2 font-normal text-muted-foreground">{row.period}</span> : null}
                </p>
                {row.bullets?.length ? (
                  <ul className="mt-1 list-disc pl-5 text-[13px]">
                    {row.bullets.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}
      {highlights?.length ? (
        <section className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.highlights}</p>
          <ul className="mt-1 list-disc pl-5 text-[13px] leading-relaxed">
            {highlights.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}
          </ul>
        </section>
      ) : null}
      {education?.length ? (
        <section className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.education}</p>
          <ul className="mt-1 space-y-1 text-[13px]">
            {education.map((row, index) => (
              <li key={`${row.diploma}-${index}`}>{[row.diploma, row.school, row.year].filter(Boolean).join("  ·  ")}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {languages?.length ? (
        <section className="mt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.languages}</p>
          <p className="mt-1 text-[13px]">{languages.join("  ·  ")}</p>
        </section>
      ) : null}
      {sections?.map((section, index) => section.paragraphs?.length ? (
        <section className="mt-4" key={`${section.kind}-${index}`}>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{section.heading}</p>
          {section.paragraphs.map((paragraph, paragraphIndex) => (
            <p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed" key={paragraphIndex}>{paragraph}</p>
          ))}
        </section>
      ) : null)}
      {cover ? (
        <section className="mt-5 border-t border-[#1B365D]/20 pt-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[#1B365D] dark:text-[#9DB4D0]">{labels.letter}</p>
          <div className="mt-2 space-y-2 text-[13px] leading-relaxed text-pretty">
            {cover.split(/\n{2,}/).map((block) => (
              <p key={block.slice(0, 24)}>{block}</p>
            ))}
          </div>
        </section>
      ) : null}
    </article>
  );
}
