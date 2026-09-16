#!/usr/bin/env node
/**
 * Build the A&E alcohol-withdrawal CIWA audit deck.
 *
 * Usage: node build_deck.js <report.json> <output.pptx>
 *
 * The report JSON is produced by alcohol_audit.analytics.full_report(). Only
 * aggregate figures are read, so nothing patient-identifiable reaches the deck.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const PptxGenJS = require("pptxgenjs");

// ---------------------------------------------------------------------------
// Design system
// ---------------------------------------------------------------------------

const C = {
  ink: "0B2B2E", // deep teal-black, used for the dark "sandwich" slides
  inkSoft: "17454A",
  teal: "04716F", // dominant colour
  sea: "13A89E", // supporting
  mist: "A8D5D1",
  alert: "C1443C", // the documentation gap, and anything that needs attention
  amber: "D98C2B",
  light: "F3F7F7",
  card: "EAF2F1",
  white: "FFFFFF",
  body: "24414A",
  muted: "63808A",
};

const FONT_HEAD = "Cambria";
const FONT_BODY = "Calibri";

const SLIDE_W = 13.3;
const SLIDE_H = 7.5;
const M = 0.6; // page margin
const CONTENT_W = SLIDE_W - M * 2;

const CHART_BASE = {
  chartColors: [C.teal, C.sea, C.mist, C.amber, C.alert],
  showLegend: false,
  catAxisLabelColor: C.muted,
  valAxisLabelColor: C.muted,
  catAxisLabelFontFace: FONT_BODY,
  valAxisLabelFontFace: FONT_BODY,
  catAxisLabelFontSize: 11,
  valAxisLabelFontSize: 11,
  valGridLine: { color: "DDE7E7", size: 1 },
  catGridLine: { style: "none" },
  dataLabelFontFace: FONT_BODY,
  dataLabelFontSize: 11,
  dataLabelColor: C.body,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const pct = (v) => `${v}%`;

function formatDate(iso) {
  if (!iso) return "unknown";
  const [y, m, d] = iso.split("-").map(Number);
  const months = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  return `${d} ${months[m - 1]} ${y}`;
}

function formatMonth(ym) {
  const [y, m] = ym.split("-").map(Number);
  const short = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${short[m - 1]} ${String(y).slice(2)}`;
}

/** Light content slide with a heading and optional kicker. */
function contentSlide(pptx, title, kicker) {
  const slide = pptx.addSlide();
  slide.background = { color: C.white };
  if (kicker) {
    slide.addText(kicker.toUpperCase(), {
      x: M, y: 0.38, w: CONTENT_W, h: 0.26,
      fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.sea,
      charSpacing: 1.6, isTextBox: true, margin: 0,
    });
  }
  slide.addText(title, {
    x: M, y: kicker ? 0.66 : 0.5, w: CONTENT_W, h: 0.72,
    fontFace: FONT_HEAD, fontSize: 32, bold: true, color: C.ink,
    isTextBox: true, margin: 0, valign: "top",
  });
  return slide;
}

/** Dark slide used for the title, the headline findings and the close. */
function darkSlide(pptx) {
  const slide = pptx.addSlide();
  slide.background = { color: C.ink };
  return slide;
}

/**
 * Pick one value font size for a whole row of stat cards, so a long value like
 * "35.7%" does not render smaller than "14%" beside it.
 */
function fitValueSize(values, cardHeight) {
  const longest = Math.max(...values.map((v) => String(v).length));
  const base = cardHeight >= 1.8 ? 42 : 30;
  if (longest <= 3) return base;
  if (longest <= 5) return Math.round(base * 0.8);
  if (longest <= 7) return Math.round(base * 0.62);
  return Math.round(base * 0.5);
}

/**
 * A tinted stat card: big number, label, optional sub-label.
 * The three blocks are stacked with explicit heights so a label that wraps to
 * two lines cannot collide with the sub-label underneath it.
 */
function statCard(slide, opts) {
  const { x, y, w, h, value, label, sub, dark = false, accent = C.teal, valueSize } = opts;
  slide.addShape("roundRect", {
    x, y, w, h,
    fill: { color: dark ? C.inkSoft : C.card },
    line: { color: dark ? C.inkSoft : C.card, width: 0 },
    rectRadius: 0.12,
    shadow: dark ? undefined : { type: "outer", color: "9FB5B5", blur: 8, offset: 1, angle: 90, opacity: 0.25 },
  });

  const padX = 0.25;
  const valueH = h * 0.44;
  const labelY = y + 0.1 + valueH;
  const labelH = h * 0.3;

  slide.addText(String(value), {
    x: x + padX, y: y + 0.1, w: w - padX * 2, h: valueH,
    fontFace: FONT_HEAD, fontSize: valueSize || fitValueSize([value], h), bold: true,
    color: dark ? C.white : accent,
    isTextBox: true, margin: 0, valign: "middle",
  });
  slide.addText(label, {
    x: x + padX, y: labelY, w: w - padX * 2, h: labelH,
    fontFace: FONT_BODY, fontSize: 13, bold: true,
    color: dark ? C.mist : C.body,
    isTextBox: true, margin: 0, valign: "top",
  });
  if (sub) {
    slide.addText(sub, {
      x: x + padX, y: labelY + labelH, w: w - padX * 2, h: Math.max(0.22, h - (labelY + labelH - y) - 0.08),
      fontFace: FONT_BODY, fontSize: 10.5, color: dark ? C.mist : C.muted,
      isTextBox: true, margin: 0, valign: "top",
    });
  }
}

/** A numbered or icon-style circle with a heading and body text beside it. */
function iconRow(slide, opts) {
  const { x, y, w, glyph, heading, body, color = C.teal } = opts;
  slide.addShape("ellipse", {
    x, y, w: 0.52, h: 0.52,
    fill: { color },
    line: { color, width: 0 },
  });
  slide.addText(glyph, {
    x, y, w: 0.52, h: 0.52,
    fontFace: FONT_HEAD, fontSize: 18, bold: true, color: C.white,
    align: "center", valign: "middle", isTextBox: true, margin: 0,
  });
  slide.addText(heading, {
    x: x + 0.74, y: y - 0.02, w: w - 0.74, h: 0.3,
    fontFace: FONT_BODY, fontSize: 15, bold: true, color: C.ink,
    isTextBox: true, margin: 0, valign: "top",
  });
  slide.addText(body, {
    x: x + 0.74, y: y + 0.28, w: w - 0.74, h: 0.62,
    fontFace: FONT_BODY, fontSize: 12.5, color: C.body,
    isTextBox: true, margin: 0, valign: "top",
  });
}

/** Footnote in the bottom margin. */
function footnote(slide, text) {
  slide.addText(text, {
    x: M, y: SLIDE_H - 0.62, w: CONTENT_W, h: 0.34,
    fontFace: FONT_BODY, fontSize: 10, color: C.muted, italic: true,
    isTextBox: true, margin: 0, valign: "middle",
  });
}

// ---------------------------------------------------------------------------
// Slides
// ---------------------------------------------------------------------------

function slideTitle(pptx, r) {
  const slide = darkSlide(pptx);
  const p = r.overview.period;

  slide.addText("Clinical audit", {
    x: M, y: 1.5, w: CONTENT_W, h: 0.3,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.sea,
    charSpacing: 2.2, isTextBox: true, margin: 0,
  });
  slide.addText("Alcohol withdrawal in the Emergency Department", {
    x: M, y: 1.9, w: 8.6, h: 1.5,
    fontFace: FONT_HEAD, fontSize: 40, bold: true, color: C.white,
    isTextBox: true, margin: 0, valign: "top", lineSpacingMultiple: 1.05,
  });
  slide.addText("CIWA-Ar scoring, treatment and outcomes", {
    x: M, y: 3.42, w: 8.6, h: 0.5,
    fontFace: FONT_BODY, fontSize: 19, color: C.mist,
    isTextBox: true, margin: 0,
  });

  slide.addShape("roundRect", {
    x: 9.5, y: 1.9, w: 3.2, h: 2.5,
    fill: { color: C.inkSoft }, line: { color: C.inkSoft, width: 0 }, rectRadius: 0.14,
  });
  slide.addText(String(r.overview.attendances), {
    x: 9.7, y: 2.1, w: 2.8, h: 0.95,
    fontFace: FONT_HEAD, fontSize: 52, bold: true, color: C.white,
    isTextBox: true, margin: 0, valign: "middle",
  });
  slide.addText("attendances audited", {
    x: 9.7, y: 3.0, w: 2.8, h: 0.3,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.mist,
    isTextBox: true, margin: 0,
  });
  slide.addText(`${r.overview.unique_patients} patients · ${p.weeks} weeks`, {
    x: 9.7, y: 3.42, w: 2.8, h: 0.3,
    fontFace: FONT_BODY, fontSize: 12, color: C.mist,
    isTextBox: true, margin: 0,
  });
  slide.addText(`${r.overview.reattenders.patients} re-attended`, {
    x: 9.7, y: 3.76, w: 2.8, h: 0.3,
    fontFace: FONT_BODY, fontSize: 12, color: C.mist,
    isTextBox: true, margin: 0,
  });

  slide.addText(`${formatDate(p.start)} to ${formatDate(p.end)}`, {
    x: M, y: 5.3, w: 8.6, h: 0.4,
    fontFace: FONT_BODY, fontSize: 15, bold: true, color: C.white,
    isTextBox: true, margin: 0,
  });
  slide.addText("Merged from the CIWA audit sheet and the A&E presenting-complaint case-note review", {
    x: M, y: 5.72, w: 8.6, h: 0.4,
    fontFace: FONT_BODY, fontSize: 12, color: C.mist,
    isTextBox: true, margin: 0,
  });
  slide.addNotes(
    `Audit of ${r.overview.attendances} A&E attendances by ${r.overview.unique_patients} patients ` +
    `presenting with alcohol intoxication or withdrawal, between ${formatDate(p.start)} and ${formatDate(p.end)}. ` +
    `The question behind the audit: are we scoring these patients with CIWA-Ar, treating them accordingly, ` +
    `and rescoring them to check they respond?`
  );
  return slide;
}

function slideMethod(pptx, r) {
  const slide = contentSlide(pptx, "What was audited, and how", "Method");
  const q = r.data_quality;

  iconRow(slide, {
    x: M, y: 1.75, w: 6.0, glyph: "1",
    heading: "Two sources, one attendance list",
    body: "The CIWA audit sheet holds every attendance in the window. Sheet 3 of the presenting-complaint " +
          "workbook holds the case-note review that completes the later ones.",
  });
  iconRow(slide, {
    x: M, y: 3.0, w: 6.0, glyph: "2",
    heading: "Matched on patient and arrival time",
    body: "Patient ID alone is not unique here, because several patients re-attend within days. " +
          "Each attendance is keyed on patient plus arrival date and time.",
  });
  iconRow(slide, {
    x: M, y: 4.25, w: 6.0, glyph: "3",
    heading: "Case-note review takes precedence",
    body: "Where both sources describe the same attendance, the completed case-note review is used " +
          "and the master sheet fills any gap it leaves.",
  });
  iconRow(slide, {
    x: M, y: 5.5, w: 6.0, glyph: "4",
    heading: "\"Treated\" means withdrawal treatment",
    body: "A benzodiazepine or thiamine (Pabrinex). IV fluids alone are counted as supportive care " +
          "and reported separately.",
    color: C.amber,
  });

  const panelX = 7.3;
  slide.addShape("roundRect", {
    x: panelX, y: 1.7, w: CONTENT_W - (panelX - M), h: 4.45,
    fill: { color: C.card }, line: { color: C.card, width: 0 }, rectRadius: 0.14,
  });
  slide.addText("Completeness of the audit trail", {
    x: panelX + 0.35, y: 1.95, w: 4.7, h: 0.35,
    fontFace: FONT_BODY, fontSize: 14, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });

  const rows = [
    ["Attendances in the merged cohort", `${r.overview.attendances}`],
    ["Case notes found and reviewed", `${r.overview.case_notes_reviewed.count} (${pct(r.overview.case_notes_reviewed.percent)})`],
    ["Case notes could not be located", `${q.case_notes_unavailable.count}`],
    ["No audit entry completed at all", `${q.no_audit_record.count}`],
    ["Outcome not recorded", `${q.outcome_not_recorded.count}`],
  ];
  rows.forEach(([label, value], i) => {
    const y = 2.45 + i * 0.66;
    slide.addText(label, {
      x: panelX + 0.35, y, w: 3.3, h: 0.5,
      fontFace: FONT_BODY, fontSize: 12, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addText(value, {
      x: panelX + 3.65, y, w: 1.4, h: 0.5,
      fontFace: FONT_HEAD, fontSize: 16, bold: true, color: C.teal,
      align: "right", isTextBox: true, margin: 0, valign: "middle",
    });
  });

  footnote(slide, "Denominators are stated on every slide, because the audit has three different populations: all attendances, those with case notes reviewed, and those with a CIWA score.");
  slide.addNotes(
    "The merge is on patient ID plus arrival datetime. Every one of the 61 case-note review rows matched an " +
    "attendance already on the master list, so no attendance is double-counted and none was lost."
  );
  return slide;
}

function slideCohort(pptx, r) {
  const slide = contentSlide(pptx, "Who came through the door", "The cohort");
  const o = r.overview;

  const cardW = (CONTENT_W - 0.45 * 3) / 4;
  const cards = [
    { value: o.attendances, label: "Attendances", sub: `${o.period.attendances_per_week} per week` },
    { value: o.unique_patients, label: "Individual patients", sub: `${o.reattenders.patients} re-attended, up to ${o.reattenders.max_visits_by_one_patient} times` },
    { value: o.age_years.median, label: "Median age (years)", sub: `range ${o.age_years.min} to ${o.age_years.max}` },
    { value: `${o.length_of_stay_hours.median}h`, label: "Median time in ED", sub: `longest ${o.length_of_stay_hours.max}h` },
  ];
  const cohortSize = fitValueSize(cards.map((c) => c.value), 1.85);
  cards.forEach((card, i) => {
    statCard(slide, {
      x: M + i * (cardW + 0.45), y: 1.72, w: cardW, h: 1.85,
      value: card.value, label: card.label, sub: card.sub, valueSize: cohortSize,
    });
  });

  const monthly = r.time_periods.monthly;
  slide.addChart(
    pptx.ChartType.bar,
    [{
      name: "Attendances",
      labels: monthly.map((m) => formatMonth(m.month)),
      values: monthly.map((m) => m.attendances),
    }],
    {
      ...CHART_BASE,
      x: M, y: 3.85, w: 7.0, h: 2.75,
      barDir: "col",
      showTitle: true,
      title: "Attendances per month",
      titleFontFace: FONT_BODY, titleFontSize: 13, titleColor: C.ink,
      showValue: true,
      dataLabelPosition: "outEnd",
      barGapWidthPct: 55,
      chartColors: [C.teal],
    }
  );

  const themes = r.presenting_complaints.themes.slice(0, 5);
  slide.addText("Presenting complaint themes", {
    x: 8.0, y: 3.95, w: 4.7, h: 0.3,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  themes.forEach((t, i) => {
    const y = 4.4 + i * 0.44;
    slide.addText(t.theme, {
      x: 8.0, y, w: 3.2, h: 0.36,
      fontFace: FONT_BODY, fontSize: 12, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addShape("roundRect", {
      x: 11.25, y: y + 0.07, w: Math.max(0.12, (t.attendances / themes[0].attendances) * 1.05), h: 0.22,
      fill: { color: C.sea }, line: { color: C.sea, width: 0 }, rectRadius: 0.05,
    });
    slide.addText(String(t.attendances), {
      x: 12.4, y, w: 0.5, h: 0.36,
      fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.teal,
      align: "right", isTextBox: true, margin: 0, valign: "middle",
    });
  });

  footnote(slide, `Denominator: all ${o.attendances} attendances. Complaint themes are keyword-matched and can overlap.`);
  slide.addNotes(
    `Steady demand of roughly ${o.period.attendances_per_week} alcohol-related attendances a week across the ` +
    `${o.period.weeks}-week window. A small group of frequent re-attenders accounts for ` +
    `${o.reattenders.attendances} of the ${o.attendances} attendances.`
  );
  return slide;
}

function slideHeadlines(pptx, r) {
  const slide = darkSlide(pptx);
  slide.addText("HEADLINE FINDINGS", {
    x: M, y: 0.55, w: CONTENT_W, h: 0.3,
    fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.sea,
    charSpacing: 2.2, isTextBox: true, margin: 0,
  });
  slide.addText("Four numbers that frame the audit", {
    x: M, y: 0.9, w: CONTENT_W, h: 0.7,
    fontFace: FONT_HEAD, fontSize: 32, bold: true, color: C.white,
    isTextBox: true, margin: 0,
  });

  const d = r.ciwa_documentation;
  const t = r.treatment;
  const rep = r.ciwa_repeats;
  const out = r.outcomes;

  const cardW = (CONTENT_W - 0.4 * 3) / 4;
  const cards = [
    { value: pct(d.documented.percent), label: "had a CIWA score documented",
      sub: `${d.documented.count} of ${d.documented.denominator} attendances` },
    { value: pct(t.treated.percent), label: "received withdrawal treatment",
      sub: `${t.treated.count} of ${t.treated.denominator} attendances` },
    { value: String(rep.repeated.count), label: "had the score repeated",
      sub: `of the ${rep.repeated.denominator} ever scored (${pct(rep.repeated.percent)})` },
    { value: pct(out.self_discharged.percent), label: "self-discharged",
      sub: `${out.self_discharged.count} left before or against advice` },
  ];
  const headlineSize = fitValueSize(cards.map((c) => c.value), 2.0);
  cards.forEach((card, i) => {
    statCard(slide, {
      x: M + i * (cardW + 0.4), y: 2.0, w: cardW, h: 2.0,
      value: card.value, label: card.label, sub: card.sub, dark: true,
      valueSize: headlineSize,
    });
  });

  slide.addShape("roundRect", {
    x: M, y: 4.45, w: CONTENT_W, h: 1.9,
    fill: { color: C.inkSoft }, line: { color: C.inkSoft, width: 0 }, rectRadius: 0.14,
  });
  slide.addText("The gap in one sentence", {
    x: M + 0.4, y: 4.68, w: CONTENT_W - 0.8, h: 0.34,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.sea,
    isTextBox: true, margin: 0,
  });
  slide.addText(
    `Treatment is being given largely without the score that is supposed to guide it: ` +
    `${d.treated_without_any_ciwa_score.count} of the ${d.treated_without_any_ciwa_score.denominator} treated attendances ` +
    `(${pct(d.treated_without_any_ciwa_score.percent)}) had no CIWA-Ar score recorded at all, and only ` +
    `${rep.repeated.count} attendance in the whole cohort was rescored to check the response.`,
    {
      x: M + 0.4, y: 5.05, w: CONTENT_W - 0.8, h: 1.1,
      fontFace: FONT_BODY, fontSize: 16, color: C.white,
      isTextBox: true, margin: 0, valign: "top", lineSpacingMultiple: 1.2,
    }
  );
  slide.addNotes(
    "These four numbers drive the recommendations at the end. The documentation rate is the root problem: " +
    "without a score there is no threshold to treat against and no baseline to rescore from."
  );
  return slide;
}

function slideDocumentation(pptx, r) {
  const slide = contentSlide(pptx, "The CIWA-Ar score is rarely documented", "Finding 1");
  const d = r.ciwa_documentation;

  slide.addChart(
    pptx.ChartType.bar,
    [{
      name: "Attendances",
      labels: ["Score documented", "No score documented", "Case notes unavailable", "No audit entry"],
      values: [d.documented.count, d.not_documented.count, d.notes_unavailable.count, d.no_audit_record.count],
    }],
    {
      ...CHART_BASE,
      x: M, y: 1.75, w: 7.1, h: 4.1,
      barDir: "bar",
      showValue: true,
      dataLabelPosition: "outEnd",
      barGapWidthPct: 45,
      chartColors: [C.teal, C.alert, C.amber, C.mist],
      varyColors: true,
      catAxisLabelFontSize: 12,
    }
  );

  const docSize = fitValueSize([pct(d.documented.percent), pct(d.documented_of_reviewed.percent)], 1.8);
  statCard(slide, {
    x: 7.9, y: 1.75, w: 2.3, h: 1.8,
    value: pct(d.documented.percent), label: "of all attendances",
    sub: `${d.documented.count} of ${d.documented.denominator}`, accent: C.alert, valueSize: docSize,
  });
  statCard(slide, {
    x: 10.4, y: 1.75, w: 2.3, h: 1.8,
    value: pct(d.documented_of_reviewed.percent), label: "of notes reviewed",
    sub: `${d.documented_of_reviewed.count} of ${d.documented_of_reviewed.denominator}`, accent: C.alert, valueSize: docSize,
  });

  slide.addText("What this means", {
    x: 7.9, y: 3.85, w: 4.8, h: 0.3,
    fontFace: FONT_BODY, fontSize: 14, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  slide.addText(
    [
      { text: `Even excluding the ${d.notes_unavailable.count} sets of notes that could not be found and the ` +
              `${d.no_audit_record.count} attendances with no audit entry, fewer than one in five reviewed ` +
              `attendances had a score.`, options: { bullet: true, breakLine: true } },
      { text: `Where a score was recorded, the time of scoring was captured ` +
              `${d.scoring_time_recorded.count} times out of ${d.scoring_time_recorded.denominator}.`,
        options: { bullet: true, breakLine: true } },
      { text: "Documentation did not improve over the audit window: the monthly rate stayed between " +
              `${Math.min(...d.monthly.filter((m) => m.attendances > 2).map((m) => m.percent))}% and ` +
              `${Math.max(...d.monthly.map((m) => m.percent))}%.`,
        options: { bullet: true } },
    ],
    {
      x: 7.9, y: 4.25, w: 4.8, h: 2.3,
      fontFace: FONT_BODY, fontSize: 12.5, color: C.body,
      isTextBox: true, margin: 0, valign: "top", paraSpaceAfter: 10,
    }
  );

  footnote(slide, `Denominator: all ${d.denominator_all_attendances} attendances; ${d.denominator_case_notes_reviewed} had case notes available for review.`);
  slide.addNotes(
    "A missing score is not the same as a missing set of notes, so the two are counted separately. " +
    "The second stat card is the fairer measure of clinical practice: it excludes attendances where nobody could review the notes."
  );
  return slide;
}

function slideScores(pptx, r) {
  const slide = contentSlide(pptx, "Every recorded score, and whether it was treated", "Finding 2");
  const s = r.ciwa_scores;

  const labels = s.by_score.map((row) => String(row.score));
  slide.addChart(
    [
      {
        type: pptx.ChartType.bar,
        data: [
          { name: "Treated", labels, values: s.by_score.map((row) => row.treated) },
          { name: "Not treated", labels, values: s.by_score.map((row) => row.attendances - row.treated) },
        ],
        options: { barDir: "col", barGrouping: "stacked", chartColors: [C.teal, C.alert] },
      },
    ],
    {
      ...CHART_BASE,
      x: M, y: 1.8, w: 7.5, h: 4.0,
      barDir: "col",
      barGrouping: "stacked",
      showTitle: true,
      title: "Attendances at each initial CIWA-Ar score",
      titleFontFace: FONT_BODY, titleFontSize: 13, titleColor: C.ink,
      showValue: true,
      dataLabelPosition: "ctr",
      dataLabelColor: C.white,
      // Blank out zero labels: a stacked segment of height 0 would otherwise
      // print a "0" floating above every bar.
      dataLabelFormatCode: "#,##0;;;",
      showLegend: true,
      legendPos: "b",
      legendFontFace: FONT_BODY,
      legendFontSize: 11,
      legendColor: C.body,
      barGapWidthPct: 40,
      valAxisMajorUnit: 1,
      chartColors: [C.teal, C.alert],
    }
  );
  slide.addText("Initial CIWA-Ar score", {
    x: M, y: 5.78, w: 7.5, h: 0.24,
    fontFace: FONT_BODY, fontSize: 11, color: C.muted, align: "center",
    isTextBox: true, margin: 0,
  });

  const panelX = 8.35;
  slide.addShape("roundRect", {
    x: panelX, y: 1.8, w: CONTENT_W - (panelX - M), h: 4.0,
    fill: { color: C.card }, line: { color: C.card, width: 0 }, rectRadius: 0.14,
  });
  slide.addText("By severity band", {
    x: panelX + 0.3, y: 2.0, w: 3.8, h: 0.3,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  slide.addText("Band                     n      treated", {
    x: panelX + 0.3, y: 2.38, w: 3.8, h: 0.26,
    fontFace: FONT_BODY, fontSize: 10.5, bold: true, color: C.muted,
    isTextBox: true, margin: 0,
  });
  s.by_band.forEach((band, i) => {
    const y = 2.68 + i * 0.52;
    slide.addText(band.band, {
      x: panelX + 0.3, y, w: 2.5, h: 0.44,
      fontFace: FONT_BODY, fontSize: 11.5, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addText(String(band.attendances), {
      x: panelX + 2.75, y, w: 0.5, h: 0.44,
      fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.ink,
      align: "center", isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addText(`${band.treated} (${pct(band.treated_percent)})`, {
      x: panelX + 3.2, y, w: 1.3, h: 0.44,
      fontFace: FONT_BODY, fontSize: 11.5, bold: true, color: C.teal,
      align: "right", isTextBox: true, margin: 0, valign: "middle",
    });
  });
  slide.addText(
    `${s.at_or_above_threshold.count} of the ${s.denominator_scored} scored attendances were at or above the ` +
    `treatment threshold of ${s.treatment_threshold}; ${s.at_or_above_threshold.treated} of those were treated.`,
    {
      x: panelX + 0.3, y: 4.95, w: 4.2, h: 0.75,
      fontFace: FONT_BODY, fontSize: 11, color: C.body, italic: true,
      isTextBox: true, margin: 0, valign: "top",
    }
  );

  footnote(slide, `Denominator: the ${s.denominator_scored} attendances with a recorded initial score, out of ${s.denominator_all_attendances} in the cohort. Median recorded score ${s.score_stats.median}, range ${s.score_stats.min} to ${s.score_stats.max}.`);
  slide.addNotes(
    "This is the slide that answers \"for a given score, how many patients had it and how many were treated\". " +
    "Because only 18 attendances carry a score, every bar here is a small number and none should be read as a rate. " +
    "The pattern that does hold: where a score was taken, it was almost always high, and treatment followed."
  );
  return slide;
}

function slideTreatment(pptx, r) {
  const slide = contentSlide(pptx, "What treatment was actually given", "Finding 3");
  const t = r.treatment;

  slide.addChart(
    pptx.ChartType.bar,
    [{
      name: "Attendances",
      // pptxgenjs plots bar categories bottom-up, so the array is reversed to
      // make the chart read top-down like the rest of the deck.
      labels: ["IV fluids", "Both benzo and thiamine", "Thiamine (Pabrinex)", "Benzodiazepine", "Any withdrawal treatment"],
      values: [t.iv_fluids.count, t.benzodiazepine_and_thiamine.count, t.thiamine_pabrinex.count,
               t.benzodiazepine.count, t.treated.count],
    }],
    {
      ...CHART_BASE,
      x: M, y: 1.8, w: 7.2, h: 4.0,
      barDir: "bar",
      showValue: true,
      dataLabelPosition: "outEnd",
      barGapWidthPct: 45,
      chartColors: [C.teal],
      catAxisLabelFontSize: 12,
    }
  );

  const treatSize = fitValueSize([t.treated.count, `${t.chlordiazepoxide_dose_mg.median} mg`], 1.85);
  statCard(slide, {
    x: 8.0, y: 1.8, w: 2.25, h: 1.85,
    value: t.treated.count, label: "attendances treated",
    sub: `${pct(t.treated.percent)} of ${t.treated.denominator}`, valueSize: treatSize,
  });
  statCard(slide, {
    x: 10.45, y: 1.8, w: 2.25, h: 1.85,
    value: `${t.chlordiazepoxide_dose_mg.median} mg`, label: "median chlordiazepoxide",
    sub: `range ${t.chlordiazepoxide_dose_mg.min} to ${t.chlordiazepoxide_dose_mg.max} mg`,
    accent: C.sea, valueSize: treatSize,
  });

  slide.addText("Drugs recorded", {
    x: 8.0, y: 3.95, w: 4.7, h: 0.3,
    fontFace: FONT_BODY, fontSize: 14, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  const drugLabel = { chlordiazepoxide: "Chlordiazepoxide", diazepam: "Diazepam",
                      lorazepam: "Lorazepam", thiamine: "Thiamine / Pabrinex" };
  const maxDrug = Math.max(...t.drugs_used.map((d) => d.attendances), 1);
  t.drugs_used.forEach((drug, i) => {
    const y = 4.35 + i * 0.46;
    slide.addText(drugLabel[drug.drug] || drug.drug, {
      x: 8.0, y, w: 2.4, h: 0.38,
      fontFace: FONT_BODY, fontSize: 12, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addShape("roundRect", {
      x: 10.5, y: y + 0.09, w: Math.max(0.1, (drug.attendances / maxDrug) * 1.6), h: 0.2,
      fill: { color: C.sea }, line: { color: C.sea, width: 0 }, rectRadius: 0.05,
    });
    slide.addText(String(drug.attendances), {
      x: 12.25, y, w: 0.45, h: 0.38,
      fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.teal,
      align: "right", isTextBox: true, margin: 0, valign: "middle",
    });
  });

  footnote(slide, `Denominator: all ${t.denominator} attendances. "Treated" means a benzodiazepine or thiamine; IV fluids alone count as supportive care. Treatment time was recorded for ${t.treatment_time_recorded.count} of ${t.treatment_time_recorded.denominator} treated attendances.`);
  slide.addNotes(
    "Chlordiazepoxide is the dominant agent, with thiamine given alongside it in most treated attendances. " +
    "The pairing is what the pathway expects; the problem is that it happens without a documented score."
  );
  return slide;
}

function slideRepeats(pptx, r) {
  const slide = contentSlide(pptx, "Almost no one was rescored", "Finding 4");
  const rep = r.ciwa_repeats;

  slide.addShape("roundRect", {
    x: M, y: 1.8, w: 5.3, h: 3.9,
    fill: { color: C.card }, line: { color: C.card, width: 0 }, rectRadius: 0.14,
  });
  slide.addText(String(rep.repeated.count), {
    x: M + 0.4, y: 2.25, w: 4.5, h: 1.4,
    fontFace: FONT_HEAD, fontSize: 96, bold: true, color: C.alert,
    isTextBox: true, margin: 0, valign: "middle",
  });
  slide.addText(
    `attendance of the ${rep.denominator_scored_attendances} that had an initial CIWA-Ar score was rescored`,
    {
      x: M + 0.4, y: 3.75, w: 4.5, h: 0.85,
      fontFace: FONT_BODY, fontSize: 16, bold: true, color: C.ink,
      isTextBox: true, margin: 0, valign: "top",
    }
  );
  slide.addText(
    `That is ${rep.patients_with_a_repeat_score} patient in the entire ${r.overview.attendances}-attendance cohort.`,
    {
      x: M + 0.4, y: 4.75, w: 4.5, h: 0.7,
      fontFace: FONT_BODY, fontSize: 13, color: C.muted, italic: true,
      isTextBox: true, margin: 0, valign: "top",
    }
  );

  slide.addChart(
    pptx.ChartType.doughnut,
    [{
      name: "Repeat scoring",
      labels: ["Rescored", "Explicitly not rescored", "Not recorded"],
      values: [rep.repeated.count, rep.not_repeated.count, rep.not_recorded.count],
    }],
    {
      ...CHART_BASE,
      x: 5.35, y: 1.75, w: 3.6, h: 4.0,
      holeSize: 55,
      showLegend: true,
      legendPos: "b",
      legendFontFace: FONT_BODY,
      legendFontSize: 11,
      legendColor: C.body,
      showValue: true,
      dataLabelColor: C.white,
      dataLabelFontSize: 12,
      chartColors: [C.teal, C.alert, C.amber],
    }
  );

  slide.addText("Why it matters", {
    x: 9.2, y: 1.95, w: 3.5, h: 0.3,
    fontFace: FONT_BODY, fontSize: 14, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  slide.addText(
    [
      { text: "Symptom-triggered treatment depends on rescoring: the first score sets the dose, the repeat " +
              "tells you whether it worked.", options: { bullet: true, breakLine: true } },
      { text: `${rep.treated_without_repeat.count} of the ${rep.treated_without_repeat.denominator} treated and scored ` +
              "attendances were never rescored after treatment.", options: { bullet: true, breakLine: true } },
      { text: "Without a repeat there is no documented evidence that withdrawal was controlled before the " +
              "patient was admitted or sent home.", options: { bullet: true } },
    ],
    {
      x: 9.2, y: 2.35, w: 3.5, h: 3.0,
      fontFace: FONT_BODY, fontSize: 12.5, color: C.body,
      isTextBox: true, margin: 0, valign: "top", paraSpaceAfter: 10,
    }
  );

  footnote(slide, `Denominator: the ${rep.denominator_scored_attendances} attendances with an initial score. A repeat cannot exist without one.`);
  slide.addNotes(
    "The single rescored attendance had an initial score of 22, was treated, had two repeat scores recorded and was discharged. " +
    "It is the only complete symptom-triggered cycle in the audit."
  );
  return slide;
}

function slideOutcomes(pptx, r) {
  const slide = contentSlide(pptx, "How the attendances ended", "Finding 5");
  const o = r.outcomes;

  slide.addChart(
    pptx.ChartType.doughnut,
    [{
      name: "Outcome",
      labels: ["Discharged", "Admitted", "Self-discharged", "Died", "Not recorded"],
      values: [o.discharged.count, o.admitted.count, o.self_discharged.count, o.died.count, o.not_recorded.count],
    }],
    {
      ...CHART_BASE,
      x: M, y: 1.7, w: 4.6, h: 3.9,
      holeSize: 52,
      showLegend: true,
      legendPos: "b",
      legendFontFace: FONT_BODY,
      legendFontSize: 11,
      legendColor: C.body,
      showValue: true,
      dataLabelColor: C.white,
      dataLabelFontSize: 12,
      chartColors: [C.sea, C.teal, C.amber, C.alert, C.mist],
    }
  );

  const cardW = 2.3;
  const cards = [
    { value: o.admitted.count, label: "Admitted", sub: pct(o.admitted.percent), accent: C.teal },
    { value: o.self_discharged.count, label: "Self-discharged", sub: pct(o.self_discharged.percent), accent: C.amber },
    { value: o.discharged.count, label: "Discharged", sub: pct(o.discharged.percent), accent: C.sea },
    { value: o.died.count, label: "Died", sub: pct(o.died.percent), accent: C.alert },
  ];
  cards.forEach((card, i) => {
    statCard(slide, {
      x: 5.3 + (i % 2) * (cardW + 0.28), y: 1.75 + Math.floor(i / 2) * 1.5,
      w: cardW, h: 1.32,
      value: card.value, label: card.label, sub: card.sub, accent: card.accent,
    });
  });

  slide.addText("Treatment rate by outcome", {
    x: 10.5, y: 1.85, w: 2.2, h: 0.5,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  const rows = [
    ["Admitted", o.by_outcome.admitted],
    ["Discharged", o.by_outcome.discharged],
    ["Self-discharged", o.by_outcome.self_discharged],
  ];
  rows.forEach(([label, data], i) => {
    const y = 2.42 + i * 0.72;
    slide.addText(label, {
      x: 10.5, y, w: 2.2, h: 0.28,
      fontFace: FONT_BODY, fontSize: 11.5, color: C.body,
      isTextBox: true, margin: 0,
    });
    slide.addText(`${data.treated} of ${data.count} treated`, {
      x: 10.5, y: y + 0.26, w: 2.2, h: 0.28,
      fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.teal,
      isTextBox: true, margin: 0,
    });
  });

  slide.addShape("roundRect", {
    x: 5.3, y: 4.82, w: 7.4, h: 1.0,
    fill: { color: C.card }, line: { color: C.card, width: 0 }, rectRadius: 0.12,
  });
  slide.addText(
    `Of the ${o.self_discharged.count} who self-discharged, ${o.self_discharge_profile.left_before_being_seen.count} left before being seen for treatment ` +
    `and ${o.self_discharge_profile.ciwa_scored.count} had a CIWA-Ar score. Median time in the department before leaving: ` +
    `${o.self_discharge_profile.median_length_of_stay_hours} hours.`,
    {
      x: 5.55, y: 4.95, w: 6.9, h: 0.76,
      fontFace: FONT_BODY, fontSize: 12, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    }
  );

  footnote(slide, `Denominator: all ${o.denominator} attendances. A medical referral was made in ${o.medical_referral.count} (${pct(o.medical_referral.percent)}).`);
  slide.addNotes(
    "Self-discharge is the outcome the pathway can most plausibly change: these patients are in the department " +
    "long enough to be scored and offered treatment, and most leave without either."
  );
  return slide;
}

function slideTime(pptx, r) {
  const slide = contentSlide(pptx, "When they arrive, and how long they stay", "Time period");
  const tp = r.time_periods;

  slide.addChart(
    [
      {
        type: pptx.ChartType.bar,
        data: [{ name: "Attendances", labels: tp.monthly.map((m) => formatMonth(m.month)),
                 values: tp.monthly.map((m) => m.attendances) }],
        options: { barDir: "col", chartColors: [C.mist] },
      },
      {
        type: pptx.ChartType.line,
        data: [{ name: "Treated", labels: tp.monthly.map((m) => formatMonth(m.month)),
                 values: tp.monthly.map((m) => m.treated) },
               { name: "CIWA scored", labels: tp.monthly.map((m) => formatMonth(m.month)),
                 values: tp.monthly.map((m) => m.ciwa_scored) }],
        options: { chartColors: [C.teal, C.alert], lineSize: 3, lineSmooth: false,
                   showValue: false },
      },
    ],
    {
      ...CHART_BASE,
      x: M, y: 1.8, w: 7.2, h: 4.1,
      showTitle: true,
      title: "Attendances, treatment and scoring by month",
      titleFontFace: FONT_BODY, titleFontSize: 13, titleColor: C.ink,
      showLegend: true,
      legendPos: "b",
      legendFontFace: FONT_BODY,
      legendFontSize: 11,
      legendColor: C.body,
      barGapWidthPct: 55,
    }
  );

  slide.addText("Arrival time of day", {
    x: 8.1, y: 1.9, w: 4.6, h: 0.3,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  const maxBlock = Math.max(...tp.by_arrival_block.map((b) => b.attendances), 1);
  tp.by_arrival_block.forEach((block, i) => {
    const y = 2.3 + i * 0.5;
    slide.addText(block.block, {
      x: 8.1, y, w: 1.5, h: 0.4,
      fontFace: FONT_BODY, fontSize: 11.5, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addShape("roundRect", {
      x: 9.7, y: y + 0.1, w: Math.max(0.1, (block.attendances / maxBlock) * 2.4), h: 0.2,
      fill: { color: C.teal }, line: { color: C.teal, width: 0 }, rectRadius: 0.05,
    });
    slide.addText(String(block.attendances), {
      x: 12.25, y, w: 0.45, h: 0.4,
      fontFace: FONT_BODY, fontSize: 11.5, bold: true, color: C.teal,
      align: "right", isTextBox: true, margin: 0, valign: "middle",
    });
  });

  slide.addText("Time in the department", {
    x: 8.1, y: 4.4, w: 4.6, h: 0.3,
    fontFace: FONT_BODY, fontSize: 13, bold: true, color: C.ink,
    isTextBox: true, margin: 0,
  });
  const maxLos = Math.max(...tp.length_of_stay_bands.map((b) => b.attendances), 1);
  tp.length_of_stay_bands.forEach((band, i) => {
    const y = 4.78 + i * 0.4;
    slide.addText(band.band, {
      x: 8.1, y, w: 1.5, h: 0.32,
      fontFace: FONT_BODY, fontSize: 11.5, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addShape("roundRect", {
      x: 9.7, y: y + 0.07, w: Math.max(0.1, (band.attendances / maxLos) * 2.4), h: 0.18,
      fill: { color: C.sea }, line: { color: C.sea, width: 0 }, rectRadius: 0.05,
    });
    slide.addText(String(band.attendances), {
      x: 12.25, y, w: 0.45, h: 0.32,
      fontFace: FONT_BODY, fontSize: 11.5, bold: true, color: C.sea,
      align: "right", isTextBox: true, margin: 0, valign: "middle",
    });
  });

  footnote(slide, `Period audited: ${formatDate(tp.period.start)} to ${formatDate(tp.period.end)}, ${tp.period.days} days. Median stay ${tp.length_of_stay_hours.median} hours; ${tp.four_hour_breaches.count} of ${tp.four_hour_breaches.denominator} attendances exceeded four hours.`);
  slide.addNotes(
    "Arrivals cluster in the evening and overnight, which is when senior cover is thinnest. " +
    "Any intervention aimed at improving scoring has to work out of hours to move these numbers."
  );
  return slide;
}

function slideDataQuality(pptx, r) {
  const slide = contentSlide(pptx, "What the audit could not tell us", "Caveats");
  const q = r.data_quality;

  const items = [
    { label: "Attendances with no audit entry completed", data: q.no_audit_record },
    { label: "Case notes could not be located", data: q.case_notes_unavailable },
    { label: "Treated, but no CIWA-Ar score recorded", data: q.treated_without_ciwa_score },
    { label: "Scored, but no time of scoring recorded", data: q.scored_without_scoring_time },
    { label: "Treated, but no treatment time recorded", data: q.treated_without_treatment_time },
    { label: "Repeat scoring question left blank", data: q.repeat_scoring_not_recorded },
  ];

  const colW = (CONTENT_W - 0.4) / 2;
  items.forEach((item, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = M + col * (colW + 0.4);
    const y = 1.8 + row * 1.25;
    slide.addShape("roundRect", {
      x, y, w: colW, h: 1.05,
      fill: { color: C.card }, line: { color: C.card, width: 0 }, rectRadius: 0.1,
    });
    slide.addText(`${item.data.count} / ${item.data.denominator}`, {
      x: x + 0.28, y: y + 0.12, w: 1.8, h: 0.5,
      fontFace: FONT_HEAD, fontSize: 24, bold: true, color: C.alert,
      isTextBox: true, margin: 0, valign: "middle",
    });
    slide.addText(pct(item.data.percent), {
      x: x + 0.28, y: y + 0.62, w: 1.8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.muted,
      isTextBox: true, margin: 0, valign: "top",
    });
    slide.addText(item.label, {
      x: x + 2.2, y: y + 0.12, w: colW - 2.5, h: 0.8,
      fontFace: FONT_BODY, fontSize: 12.5, color: C.body,
      isTextBox: true, margin: 0, valign: "middle",
    });
  });

  slide.addText(
    "Small numbers throughout. Only 18 attendances carry a CIWA-Ar score, so per-score figures are counts, " +
    "not rates, and no statistical inference should be drawn from them. The audit measures what was written " +
    "down, which is not necessarily what was done at the bedside.",
    {
      x: M, y: 5.6, w: CONTENT_W, h: 0.85,
      fontFace: FONT_BODY, fontSize: 12.5, color: C.body, italic: true,
      isTextBox: true, margin: 0, valign: "top",
    }
  );
  slide.addNotes(
    "Be explicit about this in the meeting: the headline is a documentation finding. It is possible that some " +
    "patients were assessed without the score being filed, but on an audit basis an unrecorded score cannot be relied on."
  );
  return slide;
}

function slideRecommendations(pptx, r) {
  const slide = darkSlide(pptx);
  const d = r.ciwa_documentation;
  const rep = r.ciwa_repeats;

  slide.addText("WHERE TO GO NEXT", {
    x: M, y: 0.55, w: CONTENT_W, h: 0.3,
    fontFace: FONT_BODY, fontSize: 12, bold: true, color: C.sea,
    charSpacing: 2.2, isTextBox: true, margin: 0,
  });
  slide.addText("Four changes the numbers point to", {
    x: M, y: 0.9, w: CONTENT_W, h: 0.7,
    fontFace: FONT_HEAD, fontSize: 32, bold: true, color: C.white,
    isTextBox: true, margin: 0,
  });

  const recs = [
    {
      n: "1",
      heading: "Make CIWA-Ar part of triage, not an afterthought",
      body: `Only ${pct(d.documented.percent)} of attendances have a score. Trigger the chart from the ` +
            "presenting complaint so alcohol withdrawal and intoxication auto-prompt a score.",
    },
    {
      n: "2",
      heading: "Tie the rescore to the drug chart",
      body: `${rep.treated_without_repeat.count} of ${rep.treated_without_repeat.denominator} treated and scored attendances were never rescored. ` +
            "Require a repeat score before a second dose is signed for.",
    },
    {
      n: "3",
      heading: "Close the loop on the patients who leave",
      body: `${r.outcomes.self_discharged.count} attendances ended in self-discharge. Offer a score and thiamine at first contact ` +
            "rather than at the point of a decision to admit.",
    },
    {
      n: "4",
      heading: "Re-audit on the same definitions in six months",
      body: "This API reproduces every figure from the source workbooks, so the next cycle is a re-run " +
            "rather than a rebuild. Target: scoring documented in half of attendances.",
    },
  ];

  const colW = (CONTENT_W - 0.5) / 2;
  recs.forEach((rec, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const x = M + col * (colW + 0.5);
    const y = 2.0 + row * 2.2;
    slide.addShape("ellipse", {
      x, y, w: 0.6, h: 0.6,
      fill: { color: C.sea }, line: { color: C.sea, width: 0 },
    });
    slide.addText(rec.n, {
      x, y, w: 0.6, h: 0.6,
      fontFace: FONT_HEAD, fontSize: 22, bold: true, color: C.ink,
      align: "center", valign: "middle", isTextBox: true, margin: 0,
    });
    slide.addText(rec.heading, {
      x: x + 0.85, y: y - 0.04, w: colW - 0.85, h: 0.62,
      fontFace: FONT_BODY, fontSize: 16, bold: true, color: C.white,
      isTextBox: true, margin: 0, valign: "top",
    });
    slide.addText(rec.body, {
      x: x + 0.85, y: y + 0.62, w: colW - 0.85, h: 1.2,
      fontFace: FONT_BODY, fontSize: 12.5, color: C.mist,
      isTextBox: true, margin: 0, valign: "top", lineSpacingMultiple: 1.15,
    });
  });

  slide.addText(
    `Audit period ${formatDate(r.overview.period.start)} to ${formatDate(r.overview.period.end)} · ` +
    `${r.overview.attendances} attendances · ${r.overview.unique_patients} patients`,
    {
      x: M, y: 6.6, w: CONTENT_W, h: 0.35,
      fontFace: FONT_BODY, fontSize: 11, color: C.muted,
      isTextBox: true, margin: 0,
    }
  );
  slide.addNotes(
    "Recommendation 4 matters for the audit cycle: the merge and every definition used here live in code, " +
    "so the re-audit is a re-run against fresh workbooks."
  );
  return slide;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

function buildDeck(report, outputPath) {
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE"; // must be set before any slide is added
  pptx.author = "Emergency Department clinical audit";
  pptx.company = "Clinical audit";
  pptx.title = "Alcohol withdrawal in the Emergency Department: CIWA-Ar audit";
  pptx.subject = "CIWA-Ar scoring, treatment and outcomes";

  slideTitle(pptx, report);
  slideMethod(pptx, report);
  slideCohort(pptx, report);
  slideHeadlines(pptx, report);
  slideDocumentation(pptx, report);
  slideScores(pptx, report);
  slideTreatment(pptx, report);
  slideRepeats(pptx, report);
  slideOutcomes(pptx, report);
  slideTime(pptx, report);
  slideDataQuality(pptx, report);
  slideRecommendations(pptx, report);

  return pptx.writeFile({ fileName: outputPath });
}

function main() {
  const [reportPath, outputPath] = process.argv.slice(2);
  if (!reportPath || !outputPath) {
    console.error("Usage: node build_deck.js <report.json> <output.pptx>");
    process.exit(2);
  }
  const report = JSON.parse(fs.readFileSync(reportPath, "utf8"));
  buildDeck(report, path.resolve(outputPath))
    .then((file) => {
      console.log(`Wrote ${file}`);
    })
    .catch((error) => {
      console.error(error);
      process.exit(1);
    });
}

if (require.main === module) {
  main();
}

module.exports = { buildDeck };
