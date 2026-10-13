---
name: Truck Rate Manager
description: Apex Logistics' shared truck-rate desk, set like highway guide signs so the quotable price is impossible to miss.
colors:
  sign-green: "#006747"
  sign-green-deep: "#00533a"
  sign-green-soft: "#e2efe8"
  warning-yellow: "#f5c400"
  warning-soft: "#fff6cf"
  regulatory-red: "#b4232f"
  regulatory-soft: "#fbe6e7"
  apex-red: "#7a1c22"
  asphalt: "#1f2427"
  asphalt-raised: "#2b3135"
  on-asphalt: "#f3f5f4"
  on-asphalt-muted: "#b9c1bd"
  aluminium-ground: "#edefed"
  surface: "#ffffff"
  surface-sunk: "#f6f7f6"
  rule: "#d4d8d5"
  rule-strong: "#aeb4b0"
  ink: "#14181b"
  ink-secondary: "#3f474c"
  ink-muted: "#59625f"
  idle-plate: "#dfe2df"
  idle-ink: "#3a4245"
typography:
  price:
    fontFamily: "Overpass, Segoe UI, system-ui, sans-serif"
    fontSize: "44px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  headline:
    fontFamily: "Overpass, Segoe UI, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Overpass, Segoe UI, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    lineHeight: 1.15
  body:
    fontFamily: "Overpass, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Overpass, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 700
    letterSpacing: "0.06em"
  plaque:
    fontFamily: "Overpass, Segoe UI, system-ui, sans-serif"
    fontSize: "11.5px"
    fontWeight: 800
    letterSpacing: "0.05em"
    fontFeature: "tnum"
rounded:
  plaque: "5px"
  control: "6px"
  panel: "10px"
  sign: "12px"
spacing:
  xs: "6px"
  sm: "12px"
  md: "18px"
  lg: "26px"
  gutter: "28px"
components:
  button-primary:
    backgroundColor: "{colors.sign-green}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "0 16px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.sign-green-deep}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "0 14px"
    height: "40px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 11px 6px"
    height: "40px"
  sign-panel-live:
    backgroundColor: "{colors.sign-green}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sign}"
    padding: "5px"
  sign-panel-idle:
    backgroundColor: "{colors.idle-plate}"
    textColor: "{colors.idle-ink}"
    rounded: "{rounded.sign}"
    padding: "5px"
  plaque-expiring:
    backgroundColor: "{colors.warning-yellow}"
    textColor: "{colors.ink}"
    rounded: "{rounded.plaque}"
    typography: "{typography.plaque}"
  plaque-expired:
    backgroundColor: "{colors.regulatory-red}"
    textColor: "{colors.surface}"
    rounded: "{rounded.plaque}"
    typography: "{typography.plaque}"
  shell-bar:
    backgroundColor: "{colors.asphalt}"
    textColor: "{colors.on-asphalt}"
    height: "60px"
---

# Design System: Truck Rate Manager

## Overview

**Creative North Star: "The Overhead Gantry"**

Truck Rate Manager is an Apex Logistics working tool for people quoting trucking rates, often with a customer on the line. The system borrows the US highway guide sign: a green panel with a white inset border carries the one thing you act on, set big enough to read at a glance, and a plaque bolted beneath it says whether you may act on it. The world lends type, palette, density and that one signature; navigation, tables, forms and dialogs stay standard web controls on a calm aluminium ground under a charcoal asphalt bar.

Colour is semantic before it is decorative. Green means "quotable today", yellow means "ending soon", red means "expired: do not quote", white means "not started yet", gray means "no rate / not served". Everything else is neutral, so the status vocabulary never competes with chrome. Hierarchy comes from scale: prices are large, and everything around them stays small and quiet.

**Key Characteristics:**
- Guide-sign green panels (white inset border) reserved for live, quotable prices.
- MUTCD-style plaques as the single status vocabulary, everywhere a price appears.
- One price form at three sizes: gantry sign, area sign, inline token.
- Overpass (Highway Gothic lineage) for every role, with tabular numerals for all money, dates and ZIPs.
- Charcoal asphalt shell with yellow focus and a yellow current-tab marker; an Apex dark-red stripe on the platform bar.
- Light, cool aluminium ground for bright office use.

## Colors

A restrained neutral ground carrying a full road-sign status palette, where each hue has exactly one meaning.

### Primary
- **Guide-Sign Green** (#006747): live prices only, meaning sign panels, price tokens, active plaques and the primary action (Check rate, Save, Renew selected). Pressed/hover deepens to **Sign Green Deep** (#00533a); selected rows and chosen options tint with **Sign Green Wash** (#e2efe8).

### Secondary
- **Warning Yellow** (#f5c400): "ends soon" plaques and bands, the current-tab marker, focus rings on the asphalt bar, and text selection. Matrix cells that are expiring take **Warning Wash** (#fff6cf).
- **Regulatory Red** (#b4232f): expired plaques ("do not quote"), the Renew button on an expired sign, and destructive icon buttons. Expired matrix cells and error messages take **Regulatory Wash** (#fbe6e7).

### Tertiary
- **Apex Red** (#7a1c22): the company identity only, as the 3px stripe above the platform bar. Never used for status or controls, so it can't be confused with Regulatory Red.

### Neutral
- **Asphalt** (#1f2427) / **Raised Asphalt** (#2b3135): the app shell bar, the gantry beam, the Today flag; raised asphalt for controls inside the bar. The platform bar sits one step darker (#15191b).
- **On-Asphalt** (#f3f5f4) and **On-Asphalt Muted** (#b9c1bd): text on the bar and beam.
- **Aluminium Ground** (#edefed): page background.
- **Surface** (#ffffff) and **Surface Sunk** (#f6f7f6): forms, tables, dialogs, table headers.
- **Rule** (#d4d8d5) / **Rule Strong** (#aeb4b0): dividers and control borders.
- **Ink** (#14181b), **Ink Secondary** (#3f474c), **Ink Muted** (#59625f): text tiers.
- **Idle Plate** (#dfe2df) with **Idle Ink** (#3a4245): signs that carry no quotable price.

### Named Rules
**The Green Means Quotable Rule.** Sign green fills only a price that is valid on the date shown, plus the primary action. A price that is expired, upcoming or missing never sits on green.

**The One Meaning Rule.** Yellow, red, white and gray plaques each mean exactly one status. Never reuse a status hue for decoration or emphasis.

## Typography

**Display Font:** Overpass (self-hosted variable font, `fonts/overpass-latin-wght-normal.woff2`), with Segoe UI and system-ui as fallback
**Body Font:** Overpass
**Label Font:** Overpass, uppercase with tracking

**Character:** One workhorse grotesk descended from Highway Gothic: legible at a distance, confident in heavy weights, neutral at body size. The middle dot (U+00B7) is borrowed from Segoe UI/Arial through a `unicode-range` face, because Overpass's own middle dot sits off-centre.

### Hierarchy
- **Price** (800, 44px on the gantry, 36px on the area sign, 13px in tokens; line-height 1; tabular): the answer. Always the largest thing near it.
- **Headline** (800, 28px area name / 24px view titles, 1.15): view and area headings.
- **Title** (800, 22px, 1.15): sign destinations (area names on a gantry panel) and dialog titles.
- **Body** (400, 15px, 1.45): table rows, notes, form text.
- **Label** (700, 13px, 0.06em, uppercase on signs): "From …" lines, price units, table headers (12px), group headings.
- **Plaque** (800, 11.5px, 0.05em, uppercase, tabular): status plaques and sign bands (13px on bands).

### Named Rules
**The Scale-Only Hierarchy Rule.** The price outranks everything by size alone. Don't add boxes, colour or weight tricks to rank the answer.

**The Tabular Rule.** Money, dates, ZIPs and counts always set with tabular numerals so columns and repeated quotes align.

## Layout

A full-width app under a sticky 60px asphalt bar (static below 900px). Three views share the bar: **Quote** (default), **Areas**, **Rate matrix**. Content caps at 1480px with a 28px gutter (18px below 900px).

- **Quote:** a single-row lookup form (city, state, ZIP, kg, ship date, radius, Check rate), then the gantry: an asphalt beam naming the destination and ship date, with sign panels hanging beneath in an auto-fill grid (min 380px columns, top-aligned so idle signs stay short). Below 680px the lookup becomes two columns and the signs stack.
- **Areas:** a 330px sticky area index (pickup line, search, grouped list with price tokens) beside the area detail (today sign, validity strip, period table, rates by pickup, locations). It stacks below 900px.
- **Rate matrix:** title and status counts, a toolbar of filters, then a scrollable table with a sticky header and a validity column.

Spacing rhythm: 6 / 12 / 18 / 26px, with 22–26px between major blocks and more space above a section heading than below it.

## Elevation & Depth

Mostly flat, with tonal layering: aluminium ground, then white surfaces bordered by rules. Only signs and overlays lift.

### Shadow Vocabulary
- **Sign hang** (`box-shadow: 0 1px 0 #00000014, 0 6px 16px -8px #14181b55`): live sign panels, as if lit and hanging.
- **Selected row** (`box-shadow: 0 2px 8px -4px #14181b40`): the selected area in the index.
- **Menu** (`box-shadow: 0 12px 32px #14181b2e`): the Data menu.
- **Dialog** (`box-shadow: 0 24px 60px #14181b59`): modal dialogs, the Operation Log and the ZIP Map.

### Named Rules
**The Only Signs Hang Rule.** Cards and tables don't float; only live sign panels and overlays carry shadow.

## Shapes

Small, firm radii: plaques 5px, controls 6px, panels and tables 8–10px, signs 12px with an 8px inner face. The sign's white inset border (2.5px) and the plaque's 1.5px inner rule are the signature geometry: a coloured plate, a thin gap, and a light rule inside it. Hangers (6×16px asphalt posts) connect gantry signs to the beam on desktop. Open-ended validity segments end in an arrow point (clip-path).

## Components

### Buttons
- **Shape:** 6px radius, 40px tall (36px in the shell bar, 44px on the lookup).
- **Primary:** guide-sign green, white bold label, faint green drop shadow; hover deepens to #00533a.
- **Secondary:** white with a strong rule border and ink label; on the asphalt bar they become transparent with a #5d666b border.
- **Text button:** green bold label that underlines on hover. **Icon button:** 34px square, ink-secondary; danger variant in regulatory red with a red-wash hover.
- **Focus:** a 2px ink outline offset 2px on light surfaces, yellow on asphalt.

### Sign Panel (signature)
- Green plate (5px padding, 12px radius) with an inner face inside a 2.5px white rule. Content: "From {pickup}" label, destination title, price block(s), optional weight estimate above a hairline, and an "Open area" link.
- A **band** under the face states validity: green "Valid through…", yellow "Ends … · renew soon", red "Expired … · do not quote", white "Starts …", gray "No rate set / Not served".
- **Idle** variant (idle plate, gray rule) for anything not quotable; expired signs strike through the last price and offer a red Renew button.
- **Motion:** on a new quote, signs drop in (0.5s, ease-out `cubic-bezier(.16,1,.3,1)`, 60ms stagger, slight rotateX from the top edge). Disabled under reduced motion.

### Plaques and Price Tokens
- **Plaque:** a coloured 5px plate with a 1.5px inner rule and uppercase 11.5px label. These are the only status badges.
- **Price token:** a small green plate holding "$385.00 · $0.20/kg", followed by a plaque when the status is anything other than active.

### Validity Strip
A track over a hatched "no price" background, drawn on one shared window (180 days back, 365 ahead) so rows compare. Segments: green active, yellow expiring, gray expired, white with an ink outline for upcoming. When today has no price, the hatch turns red. An ink marker shows the reference date; the area view adds month ticks and a "Today" flag.

### Inputs / Fields
- White, 1px rule-strong border, 6px radius, 40px tall (44px in the lookup). Hover darkens the border; focus uses an ink border with a 3px green glow.
- Labels sit above in 13px bold ink-secondary, with an optional muted hint ("optional", "blank = today").

### Navigation
- Text tabs in the asphalt bar: muted at rest, white on hover, white with a 4px yellow underline when current (`aria-current="page"`).
- The pickup select, Manage pickups, the injected ZIP Map button and the Data menu (Import / Export / Templates) sit on the right.

### Dialogs
White, 12px radius, dialog shadow, title row with an SVG close button, footer with Cancel (secondary) and the primary action on the right. Escape closes them.

## Do's and Don'ts

### Do:
- **Do** put every quotable price on a guide-sign green plate and every status in a plaque.
- **Do** show prices in the same token form at every size (gantry, area sign, list, matrix).
- **Do** draw unpriced stretches of time as hatched track; never hide a gap.
- **Do** set money, dates and ZIPs with tabular numerals.
- **Do** keep controls standard (selects, inputs, tables, tabs); the sign world lends type, palette and the panel only.
- **Do** use the company name "Apex Logistics" as plain text until the real logo file is supplied.

### Don't:
- **Don't** place an expired, upcoming or missing price on green.
- **Don't** use Apex Red for status or controls; it is identity only.
- **Don't** recreate or imitate the Apex logo (dark-red italic "Apex" with swooshes).
- **Don't** add coloured side stripes or thick accent top borders to cards and dialogs.
- **Don't** turn the world into a costume: no road-shield icons, highway clip art or reflective-texture effects.
