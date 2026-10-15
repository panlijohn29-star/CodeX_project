(() => {
  'use strict';

  const DONE_KEY = 'truck-rate-manager:tour-done:v1';
  const SECTIONS = ['Views', 'Rate periods', 'Areas & locations', 'Pickups', 'Data', 'ZIP Map'];
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // section is 1-based and indexes SECTIONS; view is the tab to open first; target is a selector or a list whose union is highlighted.
  const STEPS = [
    {
      title: 'Welcome to Truck Rate Manager',
      body: 'The shared source of truth for trucking rates: which areas each pickup serves and what it charges per FTL and per kilo, over time. This two-minute tour covers the six things you will use.',
      list: SECTIONS,
    },
    {
      section: 1, view: 'Quote', target: '.view-tabs', title: 'Four views',
      body: '<b>Quote</b> answers a price question. <b>Areas</b> maintains areas, locations and rate periods. <b>Radius zones</b> builds areas from distance rings. <b>Rate matrix</b> compares and edits prices in bulk.',
    },
    {
      section: 1, view: 'Quote', target: '.lookup', title: 'Check a destination',
      body: 'Enter a City, State and ZIP. Weight is optional. Leave Ship date blank for today, or pick a date to price a future or past shipment. Nearby radius (10–200 miles) is used to suggest areas for ZIPs that are not saved yet.',
    },
    {
      section: 1, view: 'Quote', target: '.gantry', title: 'Read the answer by pickup',
      body: 'One sign per pickup shows the destination area and the price valid on the ship date, with a status plaque: <b>Active</b>, <b>Expiring soon</b>, <b>Expired</b> or <b>Upcoming</b>. Use <b>Open area</b> to jump to the details, or <b>Renew</b> an expired price. For an unknown ZIP, nearby areas are listed nearest first with <b>Add to selected area</b>.',
    },
    {
      section: 1, view: 'Rate matrix', target: '.matrix-table', title: 'Rate matrix',
      body: 'Every area against every pickup that shares the zone set, priced on the date you choose. Edit a cell and it saves when you leave the field. The last column is a validity strip: six months back to twelve ahead, hatched where there is no price.',
    },
    {
      section: 1, view: 'Areas', target: '.area-index', title: 'Areas',
      body: 'Areas are grouped and searchable, each with a price badge for the current pickup. Pick one to see its rate periods and locations. Use <b>+</b> to create a new area with a name and optional group.',
    },
    {
      section: 2, view: 'Areas', target: '.periods', title: 'Rate periods',
      body: 'Each period has a per-FTL price, a per-kilo price and optional Effective from / to dates (end date included). <b>Add</b>, <b>Edit</b> or <b>Delete</b> a period. <b>Extend</b> moves the end date. <b>Renew</b> copies the old prices into a new period and keeps the old one as history. Ended periods are <b>Locked</b> unless you are an administrator.',
    },
    {
      section: 2, view: 'Rate matrix', target: '.matrix-counts', title: 'Filter by status',
      body: 'The chips count areas by status on the chosen date. Click one to show only Expired, Expiring soon, Upcoming or No price areas, and click again to clear. Expired and upcoming prices are read-only in the matrix.',
    },
    {
      section: 2, view: 'Rate matrix', target: '.matrix .primary', title: 'Bulk renew',
      body: 'Tick areas, then <b>Renew selected</b>. Set the start and end dates and, if you want, a % price change such as 5 or -3. A preview shows every before and after price and any area that is skipped, and nothing is saved until you confirm.',
    },
    {
      section: 3, view: 'Radius zones', target: '.radius-view', title: 'Radius zones',
      body: 'Choose a center (a pickup or any other location) and tick the radii, or add your own. Each radius becomes a ring, for example 25 and 50 give 0-25 and 25-50 miles. Name the zones, optionally set a group, then <b>Show on map</b> to preview the ZIPs. A ZIP can belong to only one area per zone set, so conflicts are reviewed one by one. Nothing is created until you confirm, and prices are set afterwards in Rate matrix.',
    },
    {
      section: 3, view: 'Areas', target: '.locations', title: 'Locations',
      body: 'An area is a list of City + State + ZIP locations from the built-in ZIP database. Search, <b>Add location</b>, edit or delete. A ZIP already used by another area of the same zone set is rejected and the owning area is named.',
    },
    {
      section: 4, view: 'Areas', target: '.pickup-picker', title: 'Pickups',
      body: 'The Pickup selector chooses whose areas and prices you see. The pencil edits a pickup, and <b>Manage pickups</b> adds, edits or deletes them. Addresses are verified when saved. Pickups can share one zone set: areas are shared, prices stay separate. Deleting a pickup also deletes its rates, and its areas if it was their only user.',
    },
    {
      section: 5, view: 'Areas', target: ['details.menu > summary', 'details.menu .menu-list'], enter: () => setMenu(true), leave: () => setMenu(false), title: 'Data menu',
      body: '<b>Import CSV</b> accepts the area template, the rate template or an Export all data file, with progress and a conflict report. <b>Export all data</b> includes every period, <b>Export current rates</b> only today\'s prices. The templates are downloadable here. A backup CSV is also written automatically every day.',
    },
    {
      section: 6, view: 'Areas', target: '.zip-map-open', title: 'ZIP Map',
      body: 'Opens a Google map of the current pickup\'s ZIPs, colored by area. Filter by group, toggle ZIP points and boundaries, and click a ZIP to see its area, prices and validity date.',
    },
    {
      title: 'You are set',
      body: 'Start with a quote check, then maintain areas and rates as prices change. Replay this tour any time with the <b>Tour</b> button in the header.',
    },
  ];

  const $ = (selector, root = document) => root.querySelector(selector);
  const tabs = () => [...document.querySelectorAll('.view-tab')];
  const currentTab = () => tabs().find((tab) => tab.classList.contains('current'));
  const setMenu = (open) => { const menu = $('details.menu'); if (menu) menu.open = open; };

  let root = null;
  let state = null;

  function el(tag, className, html) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (html !== undefined) node.innerHTML = html;
    return node;
  }

  function targetRects(target) {
    const selectors = Array.isArray(target) ? target : [target];
    const rects = selectors.map((selector) => $(selector)).filter(Boolean).map((node) => node.getBoundingClientRect()).filter((rect) => rect.width > 0 && rect.height > 0);
    if (!rects.length) return null;
    const left = Math.min(...rects.map((rect) => rect.left));
    const top = Math.min(...rects.map((rect) => rect.top));
    return { left, top, right: Math.max(...rects.map((rect) => rect.right)), bottom: Math.max(...rects.map((rect) => rect.bottom)) };
  }

  function waitFor(target, timeout = 2000) {
    return new Promise((resolve) => {
      const started = performance.now();
      const poll = () => {
        if (targetRects(target)) return resolve(true);
        if (performance.now() - started > timeout) return resolve(false);
        window.setTimeout(poll, 60);
      };
      poll();
    });
  }

  async function openView(label) {
    const tab = tabs().find((item) => item.textContent.trim() === label);
    if (tab && !tab.classList.contains('current')) tab.click();
  }

  function place(rect) {
    const { spotlight, card } = state;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const gap = 14;
    const margin = 12;
    if (!rect) {
      spotlight.classList.add('is-center');
      Object.assign(spotlight.style, { left: `${vw / 2}px`, top: `${vh / 2}px`, width: '0px', height: '0px' });
      Object.assign(card.style, { left: '50%', top: '50%', transform: 'translate(-50%, -50%)' });
      return;
    }
    spotlight.classList.remove('is-center');
    const pad = 6;
    const box = {
      left: Math.max(rect.left - pad, 4),
      top: Math.max(rect.top - pad, 4),
      right: Math.min(rect.right + pad, vw - 4),
      bottom: Math.min(rect.bottom + pad, vh - 4),
    };
    Object.assign(spotlight.style, { left: `${box.left}px`, top: `${box.top}px`, width: `${box.right - box.left}px`, height: `${box.bottom - box.top}px` });
    card.style.transform = 'none';
    const cw = card.offsetWidth;
    const ch = card.offsetHeight;
    const clampX = (x) => Math.min(Math.max(x, margin), vw - cw - margin);
    const clampY = (y) => Math.min(Math.max(y, margin), vh - ch - margin);
    const options = [
      { fits: box.bottom + gap + ch <= vh - margin, left: clampX(box.left), top: box.bottom + gap },
      { fits: box.top - gap - ch >= margin, left: clampX(box.left), top: box.top - gap - ch },
      { fits: box.right + gap + cw <= vw - margin, left: box.right + gap, top: clampY(box.top) },
      { fits: box.left - gap - cw >= margin, left: box.left - gap - cw, top: clampY(box.top) },
    ];
    const choice = options.find((option) => option.fits) || { left: clampX(vw - cw - margin), top: vh - ch - margin };
    card.style.left = `${choice.left}px`;
    card.style.top = `${choice.top}px`;
  }

  function reposition() {
    if (!state) return;
    const step = STEPS[state.index];
    place(step.target ? state.found ? targetRects(step.target) : null : null);
  }

  function renderCard(step, index) {
    const { card } = state;
    const last = index === STEPS.length - 1;
    const first = index === 0;
    card.replaceChildren();
    const meta = el('div', 'tour-meta');
    meta.append(el('span', 'tour-eyebrow', step.section ? `${step.section} · ${SECTIONS[step.section - 1]}` : first ? 'Product tour' : 'Done'), el('span', 'tour-count', `${index + 1} / ${STEPS.length}`));
    card.append(meta);
    const progress = el('div', 'tour-progress');
    progress.setAttribute('aria-hidden', 'true');
    SECTIONS.forEach((_, i) => progress.append(el('span', `tour-seg${step.section ? (i + 1 < step.section ? ' is-done' : i + 1 === step.section ? ' is-current' : '') : last ? ' is-done' : ''}`)));
    card.append(progress);
    const title = el('h2', 'tour-title');
    title.id = 'tour-title';
    title.textContent = step.title;
    card.append(title, el('p', 'tour-body', step.body));
    if (step.list) {
      const list = el('ol', 'tour-list');
      step.list.forEach((name) => list.append(el('li', '', name)));
      card.append(list);
    }
    const actions = el('div', 'tour-actions');
    const skip = el('button', 'tour-skip', last ? '' : 'Skip tour');
    skip.type = 'button';
    if (last) skip.hidden = true;
    skip.addEventListener('click', () => finish());
    const back = el('button', 'tour-btn', 'Back');
    back.type = 'button';
    back.disabled = first;
    back.addEventListener('click', () => go(index - 1));
    const next = el('button', 'tour-btn tour-primary', first ? 'Start tour' : last ? 'Finish' : 'Next');
    next.type = 'button';
    next.addEventListener('click', () => (last ? finish() : go(index + 1)));
    actions.append(skip, el('span', 'tour-spacer'), back, next);
    card.append(actions);
    state.next = next;
  }

  async function go(index) {
    if (!state) return;
    const token = ++state.token;
    const previous = STEPS[state.index];
    if (previous && previous.leave) previous.leave();
    state.index = index;
    const step = STEPS[index];
    if (step.view) await openView(step.view);
    if (step.target) {
      if (step.enter) await new Promise((resolve) => window.setTimeout(resolve, 80));
      if (step.enter) step.enter();
      state.found = await waitFor(step.target);
    } else {
      state.found = false;
    }
    if (!state || token !== state.token) return;
    if (state.found) {
      const node = $(Array.isArray(step.target) ? step.target[0] : step.target);
      const tall = node.getBoundingClientRect().height > window.innerHeight * 0.6;
      node.scrollIntoView({ block: tall ? 'start' : 'center', behavior: 'auto', inline: 'nearest' });
    }
    renderCard(step, index);
    state.card.classList.remove('is-ready');
    reposition();
    window.requestAnimationFrame(() => {
      if (!state || token !== state.token) return;
      reposition();
      state.card.classList.add('is-ready');
      state.next.focus({ preventScroll: true });
    });
    state.live.textContent = `${step.title}. Step ${index + 1} of ${STEPS.length}.`;
  }

  function onKey(event) {
    if (!state) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(); return; }
    if (event.key === 'ArrowRight' && state.index < STEPS.length - 1) { event.preventDefault(); go(state.index + 1); return; }
    if (event.key === 'ArrowLeft' && state.index > 0) { event.preventDefault(); go(state.index - 1); return; }
    if (event.key === 'Tab') {
      const items = [...state.card.querySelectorAll('button:not([disabled]):not([hidden])')];
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      else if (!state.card.contains(document.activeElement)) { event.preventDefault(); first.focus(); }
    }
  }

  function finish() {
    if (!state) return;
    const { returnFocus, startTab } = state;
    const step = STEPS[state.index];
    if (step && step.leave) step.leave();
    try { localStorage.setItem(DONE_KEY, '1'); } catch (_) { /* private mode: the tour simply offers itself again */ }
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', reposition);
    document.removeEventListener('scroll', reposition, true);
    root.remove();
    root = null;
    state = null;
    if (startTab && startTab.isConnected && !startTab.classList.contains('current')) startTab.click();
    window.scrollTo(0, 0);
    if (returnFocus && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
  }

  function start() {
    if (state) return;
    const spotlight = el('div', 'tour-spotlight');
    const card = el('div', 'tour-card');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('aria-labelledby', 'tour-title');
    const live = el('div', 'tour-sr');
    live.setAttribute('role', 'status');
    root = el('div', 'tour-root');
    root.append(el('div', 'tour-blocker'), spotlight, card, live);
    // The app closes its Data menu on any outside click; clicks inside the tour must not reach it.
    root.addEventListener('click', (event) => event.stopPropagation());
    document.body.append(root);
    state = { index: -1, token: 0, found: false, spotlight, card, live, next: null, returnFocus: document.activeElement, startTab: currentTab() };
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', reposition);
    document.addEventListener('scroll', reposition, true);
    go(0);
  }

  function addButton() {
    const actions = $('.header-actions');
    if (!actions || $('.tour-open')) return false;
    const button = el('button', 'secondary tour-open', 'Tour');
    button.type = 'button';
    button.title = 'Take the product tour';
    button.addEventListener('click', start);
    actions.append(button);
    return true;
  }

  function ready() {
    return !!($('.header-actions') && $('.view-tabs') && $('.pickup-picker'));
  }

  function init() {
    const mount = () => {
      if (!ready()) return false;
      addButton();
      let seen = false;
      try { seen = localStorage.getItem(DONE_KEY) === '1'; } catch (_) { seen = true; }
      if (!seen) window.setTimeout(start, 700);
      return true;
    };
    if (!mount()) {
      const observer = new MutationObserver(() => { if (mount()) observer.disconnect(); });
      observer.observe(document.documentElement, { childList: true, subtree: true });
    }
  }

  init();
})();
