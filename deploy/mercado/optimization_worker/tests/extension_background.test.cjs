const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { webcrypto } = require('node:crypto');

function startWorker(challenge) {
  const calls = [];
  const session = {};
  let tabCreated;
  const chrome = {
    tabs: {onCreated: {addListener(handler) { tabCreated = handler; }}},
    runtime: {
      onMessage: {addListener() {}},
      onMessageExternal: {addListener() {}},
      async sendNativeMessage(host, message) {
        calls.push(message);
        return message.operation === 'watch' ? {challenge} : {ok:true};
      },
    },
    storage: {session: {
      async get(k) { return {[k]:session[k]}; },
      async set(values) { Object.assign(session, values); },
    }},
  };
  const folder = path.join(__dirname, '../chrome_extension');
  const context = vm.createContext({chrome, crypto:webcrypto, Date, Uint8Array});
  context.importScripts = (...names) => {
    for (const name of names) {
      vm.runInContext(fs.readFileSync(path.join(folder, name), 'utf8'), context, {filename:name});
    }
  };
  context.importScripts('background.js');
  return {tabCreated, calls};
}

test('new native tab is reported with broker challenge and session', async () => {
  const challenge = {request_id:'req', nonce:'n'.repeat(32), lease_epoch:1, plan_digest:'a'.repeat(64)};
  const {tabCreated, calls} = startWorker(challenge);
  await tabCreated({id:13, windowId:4});
  assert.deepEqual(calls.map(call => call.operation), ['watch', 'observe_tab']);
  assert.equal(calls[1].tab_id, 13);
  assert.equal(calls[1].window_id, 4);
  assert.equal(calls[1].nonce, challenge.nonce);
  assert.equal(calls[0].session_id, calls[1].session_id);
});

test('an unrequested new tab cannot register itself', async () => {
  const {tabCreated, calls} = startWorker(null);
  await tabCreated({id:14, windowId:4});
  assert.deepEqual(calls.map(call => call.operation), ['watch']);
});
