'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const BASE = 'chrome-extension://fixture/collector.html';
const SESSION = 'a'.repeat(32);
const REQUEST = '8ef82438-a6e9-4358-a47d-a67eb233c95c';
const NEW_REQUEST = '5ef82438-a6e9-4358-a47d-a67eb233c95c';

for (const extension of ['chrome_extension', 'shein_extension']) {
  test(extension + ' reuses the exact popup, even for concurrent opens', async () => {
    const {opener} = require('../' + extension + '/collector_popup.js');
    const windows = [{id: 10, type: 'normal', tabs: [{url: BASE + '?tab=7'}]}];
    let state = {ok: true, matches: true, request_id: REQUEST, task_id: 228, bound_tab_id: 7};
    const focused = [];
    const api = {
      runtime: {getURL: () => BASE, getContexts: async () => [], sendNativeMessage: async () => state},
      tabs: {get: async id => ({id, windowId: 10})},
      windows: {
        getAll: async () => [...windows],
        create: async ({url, type}) => {
          const item = {id: windows.length + 10, type, tabs: [{url}]};
          windows.push(item);
          return item;
        },
        update: async (id, options) => { assert.equal(options.focused, true); focused.push(id); },
      },
    };
    const open = opener(api, 'native', async () => SESSION);
    await Promise.all([open(7), open(7)]);
    assert.equal(windows.length, 2);
    assert.equal(new URL(windows[1].tabs[0].url).searchParams.get('request'), REQUEST);
    await open(7);
    assert.deepEqual(focused, [11]);
    state = {...state, request_id: NEW_REQUEST};
    await open(7);
    assert.equal(windows.length, 3);
    state = {ok: false};
    await open(7);
    assert.equal(windows.length, 3);
  });

  test(extension + ' closes only when native confirms the same successful request', async () => {
    const {watchCompletion} = require('../' + extension + '/collector_popup.js');
    let closed = 0;
    let windowType = 'popup';
    let popupTabs = [{id: 17}];
    let answer = {ok: true, complete: true, request_id: NEW_REQUEST, task_id: 228};
    let tick;
    const api = {
      tabs: {getCurrent: async () => ({id: 17, windowId: 9})},
      windows: {
        getCurrent: async () => ({type: windowType, id: 9, tabs: [{id: 17}]}),
        get: async () => ({type: windowType, id: 9, tabs: popupTabs}),
      },
      runtime: {sendNativeMessage: async (_, message) => {
        assert.deepEqual(message, {operation: 'collector_completion', request_id: REQUEST, task_id: 228});
        return answer;
      }},
    };
    watchCompletion(api, 'native', {request_id: REQUEST, task_id: 228}, id => { assert.equal(id, 9); closed++; },
      callback => { tick = callback; return 12; }, () => {});
    await tick();
    assert.equal(closed, 0);
    answer = {ok: true, complete: false, request_id: REQUEST, task_id: 228};
    await tick();
    assert.equal(closed, 0);
    answer.complete = true;
    windowType = 'normal';
    await tick();
    assert.equal(closed, 0);
    windowType = 'popup';
    popupTabs = [{id: 17}, {id: 18}];
    await tick();
    assert.equal(closed, 0);
    popupTabs = [{id: 17}];
    await tick();
    assert.equal(closed, 1);
  });
}
