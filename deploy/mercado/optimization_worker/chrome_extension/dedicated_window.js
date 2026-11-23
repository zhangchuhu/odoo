'use strict';

const inflight = new WeakMap();
const validId = value => Number.isSafeInteger(value) && value > 0;

async function findAnchors(api, controlUrl, persistentUrl) {
  const [contexts, tabs] = await Promise.all([
    api.runtime.getContexts({contextTypes: ['TAB']}),
    api.tabs.query({windowType: 'normal'}),
  ]);
  const controls = contexts.filter(c => c.contextType === 'TAB' && !c.incognito && c.documentUrl === controlUrl);
  const persists = tabs.filter(t => t.url === persistentUrl && !t.incognito &&
    (!t.pendingUrl || t.pendingUrl === persistentUrl));
  const controlTabs = tabs.filter(t => !t.incognito && (t.pendingUrl || t.url) === controlUrl);
  if (controls.length > 1 || persists.length > 1 || controlTabs.length > 1) {
    throw Error('ambiguous dedicated window');
  }
  const control = controls[0], persistent = persists[0], controlTab = controlTabs[0];
  for (const [element, id] of [[control, control?.tabId], [persistent, persistent?.id], [controlTab, controlTab?.id]]) {
    if (element && (!validId(element.windowId) || !validId(id))) throw Error('invalid window anchor');
  }
  if (new Set([control, persistent, controlTab].filter(Boolean).map(x => x.windowId)).size > 1 ||
      (control && controlTab && control.tabId !== controlTab.id) || (persistent && !persistent.pinned)) {
    throw Error('ambiguous dedicated window');
  }
  return {control, persistent, controlTab};
}

async function createOrRestore(api, generateMarker) {
  const stored = await api.storage.local.get('marker');
  const marker = stored.marker === undefined ? generateMarker() : stored.marker;
  if (typeof marker !== 'string' || !/^[a-f0-9]{32}$/.test(marker)) throw Error('invalid marker');
  if (stored.marker === undefined) await api.storage.local.set({marker});
  const controlUrl = api.runtime.getURL('dedicated-window.html') + '?marker=' + marker;
  // Chrome closes extension documents on reload. A browser-owned pinned tab survives.
  const persistentUrl = 'about:blank#odoo-collector-window-' + marker;
  let {control, persistent, controlTab} = await findAnchors(api, controlUrl, persistentUrl);
  let windowId = (control || persistent || controlTab)?.windowId;
  const created = !windowId;
  if (created) {
    windowId = (await api.windows.create({url: controlUrl, type: 'normal', focused: false}))?.id;
    if (!validId(windowId)) throw Error('window creation failed');
  } else if (!control && !controlTab) {
    await api.tabs.create({windowId, url: controlUrl, active: false});
  } else if (!control && controlTab?.discarded) {
    await api.tabs.reload(controlTab.id);
  }

  let creatingPersistent = false;
  for (let attempt = 0; attempt < 20; attempt++) {
    ({control, persistent} = await findAnchors(api, controlUrl, persistentUrl));
    if (control?.windowId === windowId && !persistent && !creatingPersistent) {
      await api.tabs.create({windowId, url: persistentUrl, pinned: true, active: false});
      creatingPersistent = true;
      continue;
    }
    if (control?.windowId === windowId && persistent?.windowId === windowId) {
      return {windowId, tabId: control.tabId, marker, created};
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw Error('window marker unavailable');
}

function ensureWindow(api, generateMarker) {
  if (!inflight.has(api)) {
    const operation = createOrRestore(api, generateMarker).finally(() => inflight.delete(api));
    inflight.set(api, operation);
  }
  return inflight.get(api);
}

globalThis.DedicatedWindow = {ensureWindow};
if (typeof module !== 'undefined') module.exports = {ensureWindow};
