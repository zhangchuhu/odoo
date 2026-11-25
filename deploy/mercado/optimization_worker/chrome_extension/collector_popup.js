'use strict';
(() => {
  const requestPattern = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
  const positiveId = value => Number.isSafeInteger(value) && value > 0;

  function scopeOf(request_id, task_id) {
    return typeof request_id === 'string' && requestPattern.test(request_id) && positiveId(task_id)
      ? {request_id, task_id} : null;
  }

  function opener(api, host, sessionId) {
    const pending = new Map();
    async function open(tabId) {
      const tab = await api.tabs.get(tabId);
      if (tab?.id !== tabId || !positiveId(tab.windowId)) throw Error('Collector target unavailable');
      const session = await sessionId();
      const base = api.runtime.getURL('collector.html');
      const state = await api.runtime.sendNativeMessage(host, {
        operation: 'binding_status', tab_id: tabId, window_id: tab.windowId, session_id: session,
      }).catch(() => null);
      const scope = state?.ok === true && state.matches === true && state.bound_tab_id === tabId
        ? scopeOf(state.request_id, state.task_id) : null;
      const contexts = await api.runtime.getContexts({contextTypes: ['TAB', 'POPUP']});
      const windows = await api.windows.getAll({populate: true, windowTypes: ['popup']});
      const candidates = [];
      for (const item of windows) {
        if (item.type !== 'popup' || item.tabs?.length !== 1 || !positiveId(item.id)) continue;
        const panel = item.tabs[0];
        const context = contexts.find(candidate => candidate.windowId === item.id && candidate.tabId === panel.id);
        const href = context?.documentUrl || panel.pendingUrl || panel.url;
        if (!href?.startsWith(base + '?')) continue;
        const params = new URL(href).searchParams;
        if (params.get('tab') !== String(tabId) || params.get('popup_session') !== session) continue;
        const request = params.get('request');
        if (request && typeof state?.request_id === 'string' && request !== state.request_id) continue;
        if (scope && request && (request !== scope.request_id || params.get('task') !== String(scope.task_id))) continue;
        candidates.push({id: item.id, exact: Boolean(request && scope)});
      }
      candidates.sort((a, b) => Number(b.exact) - Number(a.exact) || b.id - a.id);
      for (const item of candidates) {
        try {
          await api.windows.update(item.id, {focused: true});
          return;
        } catch (_) { /* The user may have just closed the popup. */ }
      }
      const url = new URL(base);
      url.searchParams.set('tab', String(tabId));
      url.searchParams.set('popup_session', session);
      if (scope) {
        url.searchParams.set('request', scope.request_id);
        url.searchParams.set('task', String(scope.task_id));
      }
      const window = await api.windows.create({url: url.href, type: 'popup', width: 520, height: 760});
      if (!positiveId(window?.id)) throw Error('Could not open collector popup');
    }
    return tabId => {
      if (!positiveId(tabId)) throw Error('Invalid collector tab');
      if (!pending.has(tabId)) pending.set(tabId, open(tabId).finally(() => pending.delete(tabId)));
      return pending.get(tabId);
    };
  }

  function watchCompletion(api, host, scope, close,
    repeat = (...args) => globalThis.setInterval(...args), cancel = id => globalThis.clearInterval(id)) {
    let busy = false;
    let stopped = false;
    const timer = repeat(async () => {
      if (busy || stopped) return;
      busy = true;
      try {
        const tab = await api.tabs.getCurrent();
        if (!positiveId(tab?.id) || !positiveId(tab.windowId)) return;
        const ownsPopup = popup => popup?.type === 'popup' && tab.windowId === popup.id
          && popup.tabs?.length === 1 && popup.tabs[0].id === tab.id;
        if (!ownsPopup(await api.windows.getCurrent({populate: true}))) return;
        const answer = await api.runtime.sendNativeMessage(host, {operation: 'collector_completion', ...scope});
        if (answer?.ok === true && answer.complete === true && answer.request_id === scope.request_id
            && answer.task_id === scope.task_id) {
          if (!ownsPopup(await api.windows.get(tab.windowId, {populate: true}))) return;
          await close(tab.windowId);
          stopped = true;
          cancel(timer);
        }
      } catch (_) { /* An unavailable host cannot confirm completion. */ }
      finally { busy = false; }
    }, 10000);
  }

  function monitor(api, host, page, target) {
    let watching;
    const url = new URL(page.location.href);
    function follow(scope) {
      if (!scope || watching) return;
      watching = scope;
      watchCompletion(api, host, scope, windowId => api.windows.remove(windowId));
    }
    const initial = scopeOf(url.searchParams.get('request'), Number(url.searchParams.get('task')));
    if (url.searchParams.has('popup_session')) follow(initial);
    return (state, session) => {
      if (state?.ok !== true || state.matches !== true || state.bound_tab_id !== target) return;
      const scope = scopeOf(state.request_id, state.task_id);
      if (!scope || (watching && (watching.request_id !== scope.request_id || watching.task_id !== scope.task_id))) return;
      const current = new URL(page.location.href);
      current.searchParams.set('request', scope.request_id);
      current.searchParams.set('task', String(scope.task_id));
      current.searchParams.set('popup_session', session);
      if (current.href !== page.location.href) page.history.replaceState(null, '', current.href);
      follow(scope);
    };
  }

  globalThis.CollectorPopup = {opener, monitor};
  if (typeof module !== 'undefined') module.exports = {opener, monitor, watchCompletion};
})();
