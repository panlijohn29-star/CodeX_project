(() => {
  'use strict';

  const STORE_ENDPOINT = '/api/truck-rate/store';
  const COLORS = ['#2563eb', '#dc2626', '#059669', '#7c3aed', '#d97706', '#0891b2', '#be123c', '#4f46e5', '#65a30d', '#c2410c'];
  let mapsPromise;

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
  const areaLabel = (area) => area.name || 'Unnamed area';
  const money = (value, suffix = '') => value === '' || value == null ? '—' : `$${Number(value).toFixed(2)}${suffix}`;

  function loadMaps() {
    if (typeof window.google?.maps?.Map === 'function') return Promise.resolve(window.google.maps);
    if (mapsPromise) return mapsPromise;
    const key = window.__TRUCK_RATE_GOOGLE_MAPS_API_KEY;
    if (!key) return Promise.reject(new Error('Google Maps is not configured. Add TRUCK_RATE_GOOGLE_MAPS_API_KEY to show the ZIP map.'));
    mapsPromise = new Promise((resolve, reject) => {
      const callbackName = '__truckRateZipMapReady';
      const script = document.createElement('script');
      window[callbackName] = () => {
        delete window[callbackName];
        if (typeof window.google?.maps?.Map === 'function') {
          resolve(window.google.maps);
        } else {
          reject(new Error('Google Maps loaded without its map renderer. Check the Maps JavaScript API configuration.'));
        }
      };
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=weekly&loading=async&callback=${callbackName}`;
      script.async = true;
      script.onerror = () => {
        delete window[callbackName];
        reject(new Error('Google Maps could not be loaded. ZIP points are unavailable until the map service is reachable.'));
      };
      document.head.appendChild(script);
    });
    return mapsPromise;
  }

  async function loadStore() {
    const response = await fetch(STORE_ENDPOINT, { credentials: 'same-origin' });
    const payload = await response.json();
    if (!response.ok || !payload.exists || !payload.store) throw new Error(payload.error || 'Truck Rate data is unavailable.');
    return payload;
  }

  function mapRecords(store, group) {
    return (store.areas || []).flatMap((area, index) => (area.locations || []).map((location) => ({
      area,
      color: COLORS[index % COLORS.length],
      location,
    }))).filter((record) => !group || (record.area.group || 'Ungrouped') === group);
  }

  function details(record) {
    const { area, location } = record;
    return `<div class="zip-map-info"><strong>${escapeHtml(location.zip)}</strong><span>${escapeHtml(location.city)}, ${escapeHtml(location.state)}</span><span>${escapeHtml(areaLabel(area))}${area.group ? ` · ${escapeHtml(area.group)}` : ''}</span><span>FTL: ${escapeHtml(money(area.ftlRate))} · Per kg: ${escapeHtml(money(area.perKiloRate, '/kg'))}</span></div>`;
  }

  function createDialog() {
    const dialog = document.createElement('dialog');
    dialog.className = 'zip-map-dialog';
    dialog.innerHTML = `
      <div class="zip-map-shell">
        <aside class="zip-map-sidebar">
          <div class="zip-map-title-row"><div><h2 id="zip-map-title">ZIP Map</h2><p>ZIPs are colored by Area/Zone.</p></div><button class="zip-map-close" type="button" aria-label="Close ZIP map">×</button></div>
          <label class="zip-map-control">Group <select class="zip-map-group" aria-label="Filter ZIP map by group"></select></label>
          <label class="zip-map-toggle"><input class="zip-map-points" type="checkbox" checked> Show ZIP points</label>
          <label class="zip-map-toggle"><input class="zip-map-boundaries" type="checkbox" checked> Show ZIP boundaries</label>
          <p class="zip-map-status" role="status"></p>
          <section class="zip-map-legend" aria-label="Area and Zone legend"></section>
        </aside>
        <section class="zip-map-map-wrap" aria-labelledby="zip-map-title"><div class="zip-map-canvas"></div><div class="zip-map-loading" role="status">Loading ZIP map…</div></section>
      </div>`;
    document.body.appendChild(dialog);
    dialog.querySelector('.zip-map-close').addEventListener('click', () => dialog.close());
    return dialog;
  }

  async function resolvePostalPlaceIds(maps, storePayload, records, setStatus) {
    const cache = { ...(storePayload.store.postalCodePlaceIdCache || {}) };
    const unresolved = new Map();
    records.forEach((record) => {
      const key = `${record.location.state}|${record.location.zip}`;
      if (!cache[key]) unresolved.set(key, record.location);
    });
    if (!unresolved.size) return cache;
    const geocoder = new maps.Geocoder();
    const queue = [...unresolved.entries()];
    let next = 0;
    let completed = 0;
    await Promise.all(Array.from({ length: Math.min(4, queue.length) }, async () => {
      while (next < queue.length) {
        const [key, location] = queue[next++];
        try {
          const result = await geocoder.geocode({ address: `${location.zip}, ${location.state}, USA`, componentRestrictions: { country: 'US' } });
          const postal = result.results?.find((candidate) => candidate.types?.includes('postal_code'));
          if (postal?.place_id) cache[key] = postal.place_id;
        } catch (_) {
          // A boundary lookup failure is intentionally non-fatal; points still show.
        }
        completed += 1;
        setStatus(`Resolving ZIP boundaries: ${completed} of ${unresolved.size}.`);
      }
    }));
    const changed = Object.keys(cache).some((key) => cache[key] !== (storePayload.store.postalCodePlaceIdCache || {})[key]);
    if (changed) {
      const updatedStore = { ...storePayload.store, postalCodePlaceIdCache: cache };
      // Use the existing persistence bridge so its current revision stays in sync
      // with this tab. A later business edit may safely omit this optional cache.
      localStorage.setItem('truck-rate-manager:v1', JSON.stringify(updatedStore));
      storePayload.store = updatedStore;
    }
    return cache;
  }

  async function openMap() {
    const dialog = createDialog();
    dialog.showModal();
    const canvas = dialog.querySelector('.zip-map-canvas');
    const loading = dialog.querySelector('.zip-map-loading');
    const status = dialog.querySelector('.zip-map-status');
    const groupSelect = dialog.querySelector('.zip-map-group');
    const pointsToggle = dialog.querySelector('.zip-map-points');
    const boundariesToggle = dialog.querySelector('.zip-map-boundaries');
    const legend = dialog.querySelector('.zip-map-legend');
    const setStatus = (message) => { status.textContent = message; };

    try {
      const [maps, storePayload] = await Promise.all([loadMaps(), loadStore()]);
      const allGroups = [...new Set((storePayload.store.areas || []).map((area) => area.group || 'Ungrouped'))].sort((a, b) => a.localeCompare(b));
      groupSelect.innerHTML = `<option value="">All groups</option>${allGroups.map((group) => `<option value="${escapeHtml(group)}">${escapeHtml(group)}</option>`).join('')}`;
      const mapOptions = { mapTypeControl: false, streetViewControl: false, fullscreenControl: true };
      if (window.__TRUCK_RATE_GOOGLE_MAP_ID) mapOptions.mapId = window.__TRUCK_RATE_GOOGLE_MAP_ID;
      const map = new maps.Map(canvas, mapOptions);
      const infoWindow = new maps.InfoWindow();
      const markers = [];
      let boundaryLayer = null;
      let postalIds = {};
      let boundariesReady = false;
      let boundaryWarning = '';

      function visibleRecords() { return mapRecords(storePayload.store, groupSelect.value); }
      function renderLegend(records) {
        const areas = new Map();
        records.forEach((record) => areas.set(record.area.id, record));
        legend.innerHTML = '<h3>Area / Zone</h3>' + [...areas.values()].map((record) => `<div class="zip-map-legend-item"><span class="zip-map-swatch" style="background:${record.color}"></span><span>${escapeHtml(areaLabel(record.area))}</span></div>`).join('') || '<p class="zip-map-empty">No ZIPs in this group.</p>';
      }
      function renderMarkers(records) {
        markers.splice(0).forEach((marker) => marker.setMap(null));
        if (!pointsToggle.checked) return;
        records.filter((record) => Number.isFinite(Number(record.location.lat)) && Number.isFinite(Number(record.location.lng))).forEach((record) => {
          const marker = new maps.Marker({ map, position: { lat: Number(record.location.lat), lng: Number(record.location.lng) }, title: `${record.location.zip} · ${areaLabel(record.area)}`, icon: { path: maps.SymbolPath.CIRCLE, fillColor: record.color, fillOpacity: 1, strokeColor: '#ffffff', strokeWeight: 2, scale: 7 } });
          marker.addListener('click', () => { infoWindow.setContent(details(record)); infoWindow.open({ map, anchor: marker }); });
          markers.push(marker);
        });
      }
      function fitPoints(records) {
        const bounds = new maps.LatLngBounds();
        records.filter((record) => Number.isFinite(Number(record.location.lat)) && Number.isFinite(Number(record.location.lng))).forEach((record) => bounds.extend({ lat: Number(record.location.lat), lng: Number(record.location.lng) }));
        if (!bounds.isEmpty()) map.fitBounds(bounds, 42);
      }
      function applyBoundaryStyle(records) {
        if (!boundaryLayer || !boundariesReady) return;
        const byPlaceId = new Map();
        records.forEach((record) => {
          const id = postalIds[`${record.location.state}|${record.location.zip}`];
          if (id && !byPlaceId.has(id)) byPlaceId.set(id, record);
        });
        boundaryLayer.style = boundariesToggle.checked ? (options) => {
          const record = byPlaceId.get(options.feature.placeId);
          return record ? { fillColor: record.color, fillOpacity: .32, strokeColor: record.color, strokeOpacity: .9, strokeWeight: 2 } : null;
        } : null;
        if (boundariesToggle.checked && !byPlaceId.size) setStatus('No matching ZIP boundaries were found. ZIP points remain available.');
      }
      function refresh({ refit = false } = {}) {
        const records = visibleRecords();
        renderLegend(records);
        renderMarkers(records);
        applyBoundaryStyle(records);
        if (refit) fitPoints(records);
        const withoutCoordinates = records.filter((record) => !Number.isFinite(Number(record.location.lat)) || !Number.isFinite(Number(record.location.lng))).length;
        if (!boundaryWarning && !withoutCoordinates) setStatus(`${records.length} saved ZIP${records.length === 1 ? '' : 's'} shown.`);
        if (withoutCoordinates) setStatus(`${records.length - withoutCoordinates} ZIP points shown; ${withoutCoordinates} ZIP${withoutCoordinates === 1 ? '' : 's'} need coordinates.`);
      }

      groupSelect.addEventListener('change', () => refresh({ refit: true }));
      pointsToggle.addEventListener('change', () => refresh());
      boundariesToggle.addEventListener('change', () => applyBoundaryStyle(visibleRecords()));
      refresh({ refit: true });
      loading.remove();

      if (!window.__TRUCK_RATE_GOOGLE_MAP_ID) {
        boundariesToggle.checked = false;
        boundariesToggle.disabled = true;
        boundaryWarning = 'ZIP boundaries need TRUCK_RATE_GOOGLE_MAP_ID with the Postal Code boundary layer enabled. ZIP points are shown.';
        setStatus(boundaryWarning);
        return;
      }
      boundaryLayer = map.getFeatureLayer('POSTAL_CODE');
      let boundaryCheckStarted = false;
      map.addListener('idle', () => {
        if (boundaryCheckStarted) return;
        boundaryCheckStarted = true;
        // Feature-layer availability settles just after the first map idle event.
        window.setTimeout(async () => {
          if (!boundaryLayer?.isAvailable) {
            boundariesToggle.checked = false;
            boundariesToggle.disabled = true;
            boundaryWarning = 'ZIP boundary layer is unavailable for this Map ID. ZIP points are shown.';
            setStatus(boundaryWarning);
            return;
          }
          boundariesReady = true;
          boundaryLayer.addListener('click', (event) => {
            const record = visibleRecords().find((item) => postalIds[`${item.location.state}|${item.location.zip}`] === event.features?.[0]?.placeId);
            if (record) { infoWindow.setContent(details(record)); infoWindow.setPosition(event.latLng); infoWindow.open(map); }
          });
          postalIds = await resolvePostalPlaceIds(maps, storePayload, visibleRecords(), setStatus);
          refresh();
        }, 750);
      });
    } catch (error) {
      loading.textContent = error.message || 'ZIP map could not be opened.';
      loading.style.display = 'grid';
    }
  }

  function addMapButton() {
    const actions = document.querySelector('.header-actions');
    if (!actions || document.querySelector('.zip-map-open')) return false;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary zip-map-open';
    button.textContent = 'ZIP Map';
    button.addEventListener('click', openMap);
    actions.prepend(button);
    return true;
  }

  if (!addMapButton()) {
    const observer = new MutationObserver(() => { if (addMapButton()) observer.disconnect(); });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
