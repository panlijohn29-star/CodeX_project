# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: Apex Logistics operations and sales staff checking truck rates. The typical moment is a quote check: a City / State / ZIP, optionally a weight and ship date, and the need to see which pickup serves it and at what price, often while a customer or carrier is waiting. They work on office laptops and desktop monitors during business hours, alongside email, the TMS, and the phone.

Secondary: a pricing/admin person who maintains pickups, areas, ZIPs, rate periods, renewals, and CSV imports, and administrators who review the Operation Log and may edit ended rate periods.

## Product Purpose

Truck Rate Manager is the shared source of truth for Apex Logistics' trucking rates: which delivery areas (groups of State + ZIP) each pickup serves and what each pickup charges per FTL and per kilo for each area over time. Success means a quote check answers "what is the price for this ZIP from each pickup on this date?" immediately and unambiguously, and that nobody quotes an expired or upcoming price by mistake.

## Positioning

Rates are modeled per pickup and per area with dated validity periods, so one lookup compares every pickup for the same destination on a given ship date, and the tool knows when a price is active, expiring soon, expired, or not yet started.

## Operating Context

- A feature of the internal "Report Platform" (Flask app, `app_v4.py`); the page `templates/truck_rate.html` wraps the React app (source: `C:\Users\JohnPan\Documents\ChatGPT\TMS_TRUCK_RATE`, built into `static/truck_rate/`) in an iframe and hosts the admin-only Operation Log dialog.
- `static/truck_rate/assets/persistent-store.js` syncs the app's localStorage store to shared server storage with revision checks; `zip-map.js` injects a "ZIP Map" button into the app header (`.header-actions`) and opens a Google Maps dialog colored by area.
- Calendar days are America/Chicago. Money is USD; weight is kg. Distances are straight-line miles (10–200 mile radius options).
- Rates are exported as CSV (Export All Data, current rates) and backed up daily by `backup_truck_rate.py`.

## Capabilities and Constraints

- Pickups (name, street, city, state, ZIP) each use a zone set of areas; pickups may share a zone set while keeping their own prices.
- Areas have a name, optional group, and State + ZIP locations; a State + ZIP belongs to one area per zone set.
- Rate periods: per FTL and per kilo price, optional Effective from / Effective to (inclusive). Statuses: Active, Expiring soon (last 30 days), Expired, Upcoming. Actions: Add, Edit, Extend, Renew, Delete; bulk renew with optional % change. Ended periods are locked except for administrators.
- Check location: City / State / ZIP, optional weight and ship date, nearby radius; returns the price by pickup and nearby areas with an "add to area" action.
- Rate matrix: prices valid on a chosen date, inline editing, filters by status, bulk renew.
- CSV import (area template, rate template, Export All Data file) with geocoding progress and conflict reporting.
- Integration constraints: the `.header-actions` container and `.secondary` button class are used by the injected ZIP Map button; the app reads/writes localStorage key `truck-rate-manager:v1`.

## Brand Commitments

- Company: Apex Logistics.
- Logo: `static/brand/apex_logo_color.png` (280×65 px, transparent PNG, served at `/static/brand/apex_logo_color.png`). "Apex" in bold dark-red italic, "logistics" in gray italic, between a gray upper swoosh and a red lower swoosh. Use the file as supplied; do not redraw, recolor, stretch, or crop it. At 65 px tall it is sharp on high-DPI screens up to about 32 px display height.
- Brand colors, sampled from the logo file: Apex red `#9A2235`, logistics gray `#898989`. The gray is only about 3.5:1 on white, so it is fine for the logo and decoration but not for body text.
- The logo is designed for light backgrounds; no reversed (white) version has been supplied.

## Evidence on Hand

- Real operating data lives in shared storage (`instance/truck_rate_data.json` by default); e.g. the Bensenville pickup (1301 Devon Ave., Bensenville, IL 60106) and IL zones around Chicago.
- The color logo file is on hand (see Brand Commitments). No brand fonts or reversed logo are on hand. Do not fabricate them.

## Product Principles

1. The answer first: today's (or the ship date's) price per pickup is the most prominent thing on a quote check.
2. Never quote a wrong price by accident: validity status is impossible to miss wherever a price is shown.
3. Maintenance is deliberate and secondary: editing tools are reachable but do not crowd the lookup path.
4. Shared truth: every change is saved to shared storage and auditable; the UI must never imply local-only state.

## Accessibility & Inclusion

No product-specific requirement was established; meet WCAG 2.1 AA as the default for an internal desktop tool, with full keyboard operation of lookup and editing.
