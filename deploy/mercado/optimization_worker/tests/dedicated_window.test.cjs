const test = require('node:test');
const assert = require('node:assert/strict');
const {ensureWindow} = require('../chrome_extension/dedicated_window.js');
const marker = 'a'.repeat(32);
const base = 'chrome-extension://anchor/';
const controlUrl = base + 'dedicated-window.html?marker=' + marker;
const persistentUrl = 'about:blank#odoo-collector-window-' + marker;

function fixture(initial = []) {
  const saved = {marker}, windows = [], tabs = [...initial.map(tab => ({...tab}))];
  let nextTab = 100;
  function createTab({windowId, url, pinned = false, active}) {
    const tab = {windowId, id: nextTab++, url, pinned, active};
    tabs.push(tab);
    return {...tab};
  }
  const api = {
    runtime: {
      getURL: path => base + path,
      getContexts: async () => tabs.filter(t => !t.discarded && t.url.startsWith(base)).map(t =>
        ({contextType: 'TAB', windowId: t.windowId, tabId: t.id, documentUrl: t.url})),
    },
    storage: {local: {get: async () => ({...saved}), set: async value => Object.assign(saved, value)}},
    tabs: {
      query: async () => tabs.map(t => ({...t})),
      create: async spec => createTab(spec),
      reload: async id => { tabs.find(t => t.id === id).discarded = false; },
    },
    windows: {create: async spec => {
      const id = 70 + windows.length;
      windows.push(spec);
      createTab({windowId: id, url: spec.url});
      return {id};
    }},
  };
  const ensure = () => ensureWindow(api, () => marker);
  return {api, saved, tabs, windows, ensure};
}

test('creates both anchors once then restores the same normal window', async () => {
  const f = fixture(); delete f.saved.marker;
  const first = await f.ensure();
  assert.equal(first.created, true);
  assert.equal(f.saved.marker, marker);
  assert.ok(f.tabs.some(t => t.windowId === first.windowId && t.url === persistentUrl && t.pinned && t.active === false));
  assert.deepEqual(await f.ensure(), {...first, created: false});
  assert.equal(f.windows.length, 1);
  assert.equal(f.tabs.length, 2);
});

test('migrates a live legacy control then recovers in place after extension documents close', async () => {
  const owned = {windowId: 42, id: 12, url: controlUrl};
  const user = {windowId: 8, id: 13, url: 'https://example.com'};
  const f = fixture([owned, user]);
  const first = await f.ensure();
  assert.deepEqual(first, {windowId: 42, tabId: 12, marker, created: false});
  f.tabs.splice(f.tabs.findIndex(t => t.url === controlUrl), 1);
  const restored = await f.ensure();
  assert.equal(restored.windowId, 42);
  assert.notEqual(restored.tabId, 12);
  assert.equal(f.windows.length, 0);
  assert.deepEqual(f.tabs.find(t => t.windowId === 8), user);
  assert.equal(f.tabs.filter(t => t.url === persistentUrl).length, 1);
});

test('restores with new browser window and tab IDs using the unique pinned anchor', async () => {
  const f = fixture([{windowId: 203, id: 201, url: persistentUrl, pinned: true}]);
  const result = await f.ensure();
  assert.equal(result.windowId, 203);
  assert.equal(f.tabs.find(t => t.id === result.tabId).url, controlUrl);
  assert.equal(f.windows.length, 0);
});

test('rehydrates a discarded extension document without creating a duplicate', async () => {
  const f = fixture([{windowId: 3, id: 6, url: controlUrl, discarded: true}, {windowId: 3, id: 7, url: persistentUrl, pinned: true}]);
  assert.equal((await f.ensure()).tabId, 6);
  assert.equal(f.tabs.length, 2);
});

test('refuses duplicate, unpinned or split anchors before creating any tabs', async () => {
  for (const initial of [
    [{windowId: 1, id: 2, url: controlUrl}, {windowId: 9, id: 4, url: controlUrl}],
    [{windowId: 1, id: 2, url: persistentUrl, pinned: true}, {windowId: 9, id: 4, url: persistentUrl, pinned: true}],
    [{windowId: 9, id: 2, url: controlUrl}, {windowId: 5, id: 4, url: persistentUrl, pinned: true}],
    [{windowId: 5, id: 4, url: persistentUrl, pinned: false}],
  ]) {
    const f = fixture(initial);
    await assert.rejects(f.ensure, /ambiguous/);
    assert.deepEqual(f.tabs, initial);
    assert.equal(f.windows.length, 0);
  }
});

test('creates a fresh window if all proof is gone and ignores lookalike or navigating tabs', async () => {
  const user = [
    {windowId: 7, id: 2, url: 'https://example.com/' + marker},
    {windowId: 7, id: 3, url: persistentUrl + 'suffix', pinned: true},
    {windowId: 7, id: 4, url: persistentUrl, pendingUrl: 'https://example.com/', pinned: true},
  ];
  const f = fixture(user);
  const result = await f.ensure();
  assert.equal(result.created, true);
  assert.notEqual(result.windowId, 7);
  assert.deepEqual(f.tabs.filter(t => t.windowId === 7), user);
});

test('concurrent preparation is single flight', async () => {
  const f = fixture();
  const [a, b] = await Promise.all([f.ensure(), f.ensure()]);
  assert.deepEqual(a, b);
  assert.equal(f.windows.length, 1);
  assert.equal(f.tabs.length, 2);
});
