(() => {
  'use strict';

  const key = 'truck-rate-manager:v1';
  const endpoint = '/api/truck-rate/store';
  const originalSetItem = Storage.prototype.setItem;
  let revision = 0;
  let pendingValue = null;
  let saving = false;

  function useServerCopy(result) {
    revision = Number.isInteger(result.revision) && result.revision >= 0 ? result.revision : 0;
    if (result.store && typeof result.store === 'object') {
      originalSetItem.call(localStorage, key, JSON.stringify(result.store));
    }
  }

  function migrationPayload() {
    return JSON.stringify({ revision: 0, store: JSON.parse(localStorage.getItem(key)) });
  }

  // The React bundle reads localStorage synchronously during startup. Load the
  // shared server copy first so a code deployment never becomes a data deployment.
  try {
    const request = new XMLHttpRequest();
    request.open('GET', endpoint, false);
    request.send();
    if (request.status === 200) {
      const result = JSON.parse(request.responseText);
      if (result.exists) {
        useServerCopy(result);
      } else if (localStorage.getItem(key)) {
        // One-time migration of data saved by the earlier browser-only app.
        const migration = new XMLHttpRequest();
        migration.open('PUT', endpoint, false);
        migration.setRequestHeader('Content-Type', 'application/json');
        migration.send(migrationPayload());
        if (migration.status === 200 || migration.status === 409) {
          useServerCopy(JSON.parse(migration.responseText));
        }
      }
    }
  } catch (error) {
    // Keep the existing offline/browser copy usable if the server is unavailable.
    console.warn('Truck Rate server storage is unavailable; using this browser copy.', error);
  }

  function handleConflict(result) {
    pendingValue = null;
    useServerCopy(result);
    window.alert('Another user saved Truck Rate changes first. Your conflicting change was not saved; the page will now reload the latest shared data.');
    window.location.reload();
  }

  function savePendingValue() {
    if (saving || pendingValue === null) return;
    const value = pendingValue;
    pendingValue = null;
    saving = true;
    fetch(endpoint, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision, store: JSON.parse(value) }),
    }).then(async (response) => {
      const result = await response.json();
      if (response.status === 409) {
        handleConflict(result);
        return;
      }
      if (!response.ok) throw new Error(result.error || 'Save failed.');
      useServerCopy(result);
    }).catch((error) => {
      console.warn('Truck Rate changes are saved in this browser but not yet on the server.', error);
    }).finally(() => {
      saving = false;
      savePendingValue();
    });
  }

  Storage.prototype.setItem = function persistTruckRateStore(storageKey, value) {
    originalSetItem.call(this, storageKey, value);
    if (this !== localStorage || storageKey !== key) return;
    pendingValue = value;
    savePendingValue();
  };
})();
