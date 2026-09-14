// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

(() => {
  const cleanUrl = raw => {
    try {
      if (typeof raw !== "string" || !raw.trim()) return "";
      const url = new URL(raw, location.href);
      if (!/^https?:$/.test(url.protocol) || url.username || url.password) return "";
      const allowed = /^(id|job_?id|project_?id|mission_?id|currentjobid|profile_?id|reference|ref)$/i;
      for (const key of [...url.searchParams.keys()]) if (!allowed.test(key)) url.searchParams.delete(key);
      if (url.hash.startsWith("#/")) {
        const route = new URL(url.hash.slice(1), url.origin);
        for (const key of [...route.searchParams.keys()]) if (!allowed.test(key)) route.searchParams.delete(key);
        url.hash = route.pathname + route.search;
      } else url.hash = "";
      return url.href;
    } catch { return ""; }
  };
  const plain = value => {
    if (value == null) return "";
    if (typeof value === "object") return plain(value.name || value.value || "");
    const doc = new DOMParser().parseFromString(String(value), "text/html");
    doc.querySelectorAll("script,style,form,input,textarea").forEach(node => node.remove());
    return (doc.body.textContent || "").trim();
  };
  const blank = () => ({ request_id: crypto.randomUUID(), kind: "mission", title: "", name: "", headline: "", company: "",
    description: "", country: "", location: "", currency: "", remote: "", posted_at: "", deadline: "", need_type: "",
    skills: [], email: "", phone: "", website: "", availability: "", daily_rate_min: null, daily_rate_max: null,
    budget_min: null, budget_max: null, salary_min: null, salary_max: null, url: cleanUrl(location.href) });
  const selected = window.getSelection();
  const anchor = selected?.anchorNode?.parentElement;
  const selection = anchor?.closest("input, textarea, [contenteditable=true]") ? "" : (selected?.toString() || "").trim().slice(0, 12000);
  const linkedin = /(^|\.)linkedin\.com$/i.test(location.hostname);
  const fromText = (text, mode) => {
    const lines = text.split(/\n/).map(line => line.trim()).filter(Boolean);
    const title = (lines[0] || "").slice(0, 240);
    const profile = /\/(?:in|u|profil|profile|freelance|freelancer|freelancers)\//i.test(location.pathname) && !/\/jobs\//.test(location.pathname);
    const row = { ...blank(), title, name: profile ? title : "", headline: profile ? (lines[1] || "").slice(0, 240) : "",
      kind: profile ? "candidate" : "mission", description: text, capture_mode: mode };
    const emails = [...new Set(text.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || [])];
    if (emails.length === 1) row.email = emails[0];
    return row;
  };
  // LinkedIn has no DOM crawl, hidden-state parsing, extra requests or pagination.
  if (linkedin) return selection ? { records: [fromText(selection, "selection")], linkedin: true }
    : { records: [], linkedin: true, error: "Sur LinkedIn, sélectionnez vous-même le texte de la fiche puis cliquez sur Navin. Vous pouvez aussi le saisir manuellement." };
  if (selection) return { records: [fromText(selection, "selection")] };
  const visible = node => {
    if (!node || node.closest("[hidden], [aria-hidden=true]")) return false;
    const style = getComputedStyle(node);
    return style.visibility !== "hidden" && style.display !== "none" && node.getClientRects().length > 0;
  };
  const visibleText = node => {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const parts = [];
    let visited = 0, length = 0;
    while (walker.nextNode() && visited++ < 15000 && length < 8000) {
      const parent = walker.currentNode.parentElement;
      if (!parent || parent.closest("script,style,nav,header,footer,form,input,textarea,select,button,[contenteditable=true]") || !visible(parent)) continue;
      const text = walker.currentNode.textContent.trim();
      if (text) { parts.push(text); length += text.length; }
    }
    return parts.join("\n").slice(0, 8000);
  };
  const profileUrl = raw => {
    const value = cleanUrl(raw);
    if (!value) return "";
    const parsed = new URL(value);
    return /\/(?:u|profile|profil|freelance|freelancer|freelancers)\/[^/]+\/?$/i.test(parsed.pathname) ? value : "";
  };
  if ([...document.querySelectorAll('input[type="password"]')].some(visible)) return { records: [], error: "Terminez la connexion puis ouvrez une fiche." };
  if (/just a moment|access denied|attention required|verify you are human/i.test(document.title)) return { records: [], error: "La plateforme demande une vérification. Effectuez-la vous-même avant l'import." };
  // Inspect rendered profile cards only. Do not follow links or read application state.
  const main = document.querySelector("main, [role=main]") || document.body;
  if (!profileUrl(location.href) && main) {
    const rows = [], seen = new Set();
    for (const link of [...main.querySelectorAll("a[href]")].slice(0, 1000)) {
      const url = profileUrl(link.href);
      if (!url || seen.has(url) || !visible(link) || new URL(url).hostname !== location.hostname) continue;
      let card = link;
      for (let depth = 0; depth < 5; depth++) {
        const parent = card.parentElement;
        if (!parent || parent === main || parent === document.body) break;
        const urls = new Set([...parent.querySelectorAll("a[href]")].map(a => profileUrl(a.href)).filter(Boolean));
        if (urls.size !== 1 || !urls.has(url)) break;
        card = parent;
        if (card.matches("article,li,[role=listitem]")) break;
      }
      const text = visibleText(card);
      if (text.length < 25) continue;
      const lines = text.split("\n").filter(Boolean);
      const heading = card.querySelector("[itemprop=name], h2, h3, h4");
      const name = (heading && visible(heading) ? heading.textContent : link.innerText.trim() || lines[0]).trim().slice(0, 180);
      const row = { ...fromText(text, "page"), kind: "candidate", name, url, title: name,
        headline: (lines.find(line => line !== name && !/^voir|^view|^contacter|^contact/i.test(line)) || "").slice(0, 240) };
      const role = card.querySelector('[itemprop="jobTitle"]');
      if (role && visible(role)) row.headline = role.textContent.trim().slice(0, 240);
      rows.push(row); seen.add(url);
      if (rows.length >= 101) break;
    }
    if (rows.length) return { records: rows.slice(0, 100), truncated: rows.length > 100, profile_list: true };
  }
  const objects = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(script => {
    if ((script.textContent || "").length > 500000) return;
    try {
      const data = JSON.parse(script.textContent);
      const queue = Array.isArray(data) ? data.slice(0, 200) : [data];
      let visited = 0;
      while (queue.length && visited++ < 400) {
        const item = queue.shift();
        if (!item || typeof item !== "object") continue;
        const types = Array.isArray(item["@type"]) ? item["@type"] : [item["@type"]];
        if (types.some(type => ["JobPosting", "Person"].includes(String(type).split("/").pop()))) objects.push(item);
        if (Array.isArray(item["@graph"])) queue.push(...item["@graph"].slice(0, 200));
        if (item.mainEntity) queue.push(item.mainEntity);
        if (Array.isArray(item.itemListElement)) queue.push(...item.itemListElement.slice(0, 100).map(entry => entry.item || entry));
      }
    } catch { /* Malformed structured data falls back to visible page text. */ }
  });
  const postings = objects.filter(item => [].concat(item["@type"]).some(type => String(type).endsWith("JobPosting")));
  const records = postings.slice(0, 100).map(item => {
    const address = (Array.isArray(item.jobLocation) ? item.jobLocation[0] : item.jobLocation)?.address || {};
    const pay = item.baseSalary || {};
    const amount = pay.value || {};
    const day = /^DAY$/i.test(amount.unitText || "");
    const year = /^YEAR$/i.test(amount.unitText || "");
    const min = Number(amount.minValue ?? amount.value), max = Number(amount.maxValue ?? amount.value);
    const country = plain(address.addressCountry);
    const desc = plain(item.description).slice(0, 12000);
    const row = { ...blank(), title: plain(item.title || item.name).slice(0, 240), company: plain(item.hiringOrganization).slice(0, 180),
      description: desc, country: /^[A-Z]{2}$/i.test(country) ? country.toUpperCase() : "",
      location: [address.addressLocality, address.addressRegion, country].map(plain).filter(Boolean).join(", "),
      kind: /CONTRACTOR|freelance|indépendant/i.test(`${item.employmentType || ""} ${desc}`) ? "mission" : "job",
      currency: plain(pay.currency), remote: item.jobLocationType === "TELECOMMUTE" ? "remote" : "",
      posted_at: /^\d{4}-\d{2}-\d{2}/.test(item.datePosted || "") ? item.datePosted.slice(0, 10) : "",
      deadline: /^\d{4}-\d{2}-\d{2}/.test(item.validThrough || "") ? item.validThrough.slice(0, 10) : "",
      url: cleanUrl(item.url) || (postings.length === 1 ? cleanUrl(location.href) : ""), capture_mode: "structured" };
    if (day) { row.daily_rate_min = min > 0 ? min : null; row.daily_rate_max = max > 0 ? max : null; }
    if (year) { row.salary_min = min > 0 ? min : null; row.salary_max = max > 0 ? max : null; }
    return row;
  }).filter(row => row.title && row.description);
  if (records.length) return { records, truncated: postings.length > 100 };
  const people = objects.filter(item => [].concat(item["@type"]).some(type => String(type).endsWith("Person")));
  const profiles = people.slice(0, 100).map(item => {
    const address = item.address || {};
    const url = profileUrl(item.url) || (people.length === 1 ? profileUrl(location.href) : "");
    const name = plain(item.name).slice(0, 180);
    // Structured people must also be visible on the page being reviewed.
    if (!url || !name || !main || !main.innerText.includes(name)) return null;
    return { ...blank(), kind: "candidate", name, title: name, headline: plain(item.jobTitle).slice(0, 240),
      description: (plain(item.description) || visibleText(main)).slice(0, 8000), url, capture_mode: "structured",
      country: /^[A-Z]{2}$/i.test(plain(address.addressCountry)) ? plain(address.addressCountry).toUpperCase() : "",
      location: [address.addressLocality, address.addressRegion, address.addressCountry].map(plain).filter(Boolean).join(", "),
      skills: [].concat(item.knowsAbout || []).map(plain).filter(Boolean).slice(0, 60) };
  }).filter(Boolean);
  if (profiles.length) return { records: profiles, truncated: people.length > 100 };
  // Malt profile pages: the visual order of name, headline, rate and location
  // varies, so derive each field from its own landmark instead of line order.
  if (/(^|\.)malt\.[a-z.]+$/i.test(location.hostname) && /\/(?:profile|freelance|freelancers)\//i.test(location.pathname)) {
    const text = visibleText(main);
    const heading = main.querySelector("h1");
    const name = (heading && visible(heading) ? heading.textContent : "").trim().slice(0, 180);
    const rateMatch = text.match(/(\d[\d\s.,]*)\s*(?:€|\$|£|CHF)\s*\/\s*(?:jour|day|journée)/i);
    const currencyMatch = text.match(/(\d[\d\s.,]*)\s*(€|\$|£|CHF)\s*\/\s*(?:jour|day)/i);
    const countryNames = { france: "FR", belgique: "BE", suisse: "CH", canada: "CA", maroc: "MA", tunisie: "TN",
      espagne: "ES", allemagne: "DE", "royaume-uni": "GB", italie: "IT", "pays-bas": "NL", "états-unis": "US", luxembourg: "LU" };
    const placeMatch = text.match(/([A-ZÉÈÀÂÔÛ][\wéèêàçîôû'-]+(?:[\s-][A-ZÉÈÀÂÔÛ][\w-]+)*),?\s+([A-Za-zÀ-ÿ'-]+)(?:\s*\(|\n|$)/);
    const country = placeMatch && countryNames[placeMatch[2].toLowerCase()] ? countryNames[placeMatch[2].toLowerCase()] : "";
    const availability = (text.match(/disponible\s+(?:dès\s+|en\s+|à partir du\s+)?([^\n.]{0,60})/i) || [])[0] || "";
    const headline = text.split("\n").find(line => line && line !== name && line.length <= 240
      && !/€|\$|£|\/jour|disponible/i.test(line) && !placeMatch?.[0].includes(line)) || "";
    const skills = [...new Set([...main.querySelectorAll('[class*="skill" i] a, [class*="skill" i] li, [data-testid*="skill" i]')]
      .filter(visible).map(node => node.textContent.trim()).filter(line => line && line.length <= 60))].slice(0, 60);
    if (name) {
      const rate = rateMatch ? Number(rateMatch[1].replace(/[\s,](?=\d{3}\b)/g, "").replace(",", ".")) : null;
      return { records: [{ ...blank(), kind: "candidate", name, title: name, headline: headline.slice(0, 240),
        description: text.slice(0, 12000), location: placeMatch?.[1] || "", country,
        skills, availability: availability.slice(0, 120),
        currency: currencyMatch ? (currencyMatch[2] === "€" ? "EUR" : currencyMatch[2] === "$" ? "USD" : currencyMatch[2] === "£" ? "GBP" : "CHF") : "",
        daily_rate_min: rate && rate > 0 ? rate : null, daily_rate_max: rate && rate > 0 ? rate : null, capture_mode: "page" }] };
    }
  }
  if (!main) return { records: [], error: "Aucun contenu de fiche trouvé." };
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  const parts = [];
  let count = 0, length = 0;
  while (walker.nextNode() && count++ < 15000 && length < 12000) {
    const parent = walker.currentNode.parentElement;
    if (!parent || parent.closest("script,style,nav,header,footer,form,input,textarea,select,button,[contenteditable=true]") || !visible(parent)) continue;
    const text = walker.currentNode.textContent.trim();
    if (text) { parts.push(text); length += text.length; }
  }
  const text = parts.join("\n").slice(0, 12000);
  if (!text) return { records: [], error: "Sélectionnez le texte de la fiche ou utilisez la saisie manuelle." };
  const row = fromText(text, "page");
  const heading = main.querySelector("h1");
  if (heading && visible(heading)) { row.title = heading.textContent.trim().slice(0, 240); if (row.kind === "candidate") row.name = row.title; }
  return { records: [row], unstructured: true };
})();
