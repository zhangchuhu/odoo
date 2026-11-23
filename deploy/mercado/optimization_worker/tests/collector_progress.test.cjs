const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

for (const [extension, platform, prefix] of [
  ['chrome_extension', 'mercado-libre-mx', 'MLM'],
  ['shein_extension', 'shein-us', ''],
]) {
  test(platform + ' keeps recovery accessible after setup is hidden', () => {
    const html = readFileSync(path.join(__dirname, '..', extension, 'collector.html'), 'utf8');
    const recover = html.indexOf('id="recover-session"');
    assert.ok(recover > html.indexOf('</form>'));
    assert.equal(html.match(/id="recover-session"/g).length, 1);
  });
  test(platform + ' restores fresh tab and advances only after durable detail confirmation', async () => {
    const queries = ['a', 'b', 'c'];
    const cardUrl = index => prefix ? 'https://articulo.mercadolibre.com.mx/MLM'+index : 'https://us.shein.com/item-p-'+index+'.html';
    const cards = query => Array.from({length:30}, (_, i) => ({query, rank:i+1, is_ad:false, title:'Title', product_id:prefix+(i+1),
      thumbnail_url:null, url:cardUrl(i+1)}));
    let accept = false;
    let screen = false;
    let screenResult;
    let rebindGroup = 3;
    let remainingTransitions = [];
    let guardedReads = 0;
    const binding = {request_id:'request123', plan_digest:'d'.repeat(64), lease_epoch:2,
      tab_id:7, window_id:11, session_id:'a'.repeat(32)};
    const details = [];
    function open() {
      const nodes = new Map();
      const get = id => {
        if (!nodes.has(id)) nodes.set(id, {value:'', textContent:'', hidden:false, addEventListener(_name, fn) { this.action = fn; }});
        return nodes.get(id);
      };
      get('record-id').value = 'rec123';
      get('detail-status').value = 'qualified';
      get('visual-category-code').value = 'dress';
      get('match-level').value = '同款';
      get('visual-features').value = 'neckline';
      const context = {
        chrome:{
          storage:{local:{get:async()=>({}),set:async()=>{}}},
          runtime:{sendMessage:async()=>({session_id:'a'.repeat(32)})},
          tabs:{sendMessage:async(_id,m)=>({page_url:cardUrl(1),identity:m.identity,identity_matches:true,product_id:prefix+'1',
            title:'Dress',category:'Dress',sold_display:screenResult === 'rejected' ? '+25 vendidos' : '500 vendidos',reviews_display:'500',rating_display:'4.5'})},
        },
        URL, Date, setTimeout, clearTimeout, location:{href:'chrome-extension://id/collector.html?tab=7'},
        document:{getElementById:get}, detailInputMessage:()=>null,
        confirmCollectorQueryCards: async (index, observations) => {
          assert.equal(index, rebindGroup);
          assert.deepEqual(JSON.parse(JSON.stringify(observations)), cards(queries[index]));
          rebindGroup++;
          return true;
        },
        collectorCheckpoint: async (op, data) => {
          if (op === 'collector_progress') {
            if (data.expected_binding) {
              assert.deepEqual(data.expected_binding, binding);
              ++guardedReads;
              if (remainingTransitions.length) throw Error(remainingTransitions.shift());
            }
            return {ok:true, task:'rec123', platform, queries, result_limit:2, binding,
              query_index:data.query_index, cards:data.query_index <= rebindGroup ? cards(queries[data.query_index]) : null,
              acknowledged:data.query_index < rebindGroup, details:data.query_index===2 && rebindGroup === 3 ? details : []};
          }
          assert.equal(op,'checkpoint_detail');
          if (screen && accept) {
            assert.equal(data.sales_only,true);
            assert.equal(data.detail.reason,'threshold_failure');
            assert.equal(data.detail.sold_display, screenResult === 'rejected' ? '+25 vendidos' : '500 vendidos');
            assert.equal(data.detail.visual_category_code,null);
          }
          if (!accept) throw Error('checkpoint_guard_pending');
          assert.equal(data.detail_index,details.length);
          if (screenResult === 'visual_required') return {ok:true,sales_screen:screenResult};
          details.push(data.detail);
          return screen ? {ok:true,sales_screen:screenResult} : {ok:true};
        },
      };
      vm.createContext(context);
      for (const file of ['session_persistence.js','collector.js']) vm.runInContext(readFileSync(path.resolve(__dirname,'..',extension,file),'utf8'),context);
      return get;
    }
    let get = open();
    await new Promise(setImmediate);
    await get('recover-session').action();
    assert.match(get('next-product').textContent, new RegExp(prefix+'1\\b'));
    await get('collect-detail').action();
    assert.equal(details.length,0);
    assert.match(get('error').textContent,/checkpoint_guard_pending/);
    accept = true;
    screen = platform === 'mercado-libre-mx';
    if (screen) {
      get('visual-category-code').value = '';
      screenResult = 'visual_required';
      await get('screen-detail-sales').action();
      assert.match(get('status').textContent, /请核验商品图片和款式/);
      assert.match(get('next-product').textContent, /MLM1\b/);
      assert.equal(details.length, 0);
      screenResult = 'rejected';
      await get('screen-detail-sales').action();
    } else await get('collect-detail').action();
    assert.equal(details.length,1);
    get = open();
    await new Promise(setImmediate);
    await get('recover-session').action();
    assert.match(get('status').textContent,/已核验 1/);
    assert.match(get('next-product').textContent, new RegExp(prefix+'2\\b'));

    rebindGroup = 0;
    get = open();
    await new Promise(setImmediate);
    await get('recover-session').action();
    assert.match(get('status').textContent, /已确认 0\/3/);
    await get('collect-search').action();
    await get('collect-search').action();
    remainingTransitions = ['checkpoint_identity_changed'];
    await get('collect-search').action();
    assert.match(get('error').textContent, /checkpoint_identity_changed/);
    assert.equal(guardedReads, 1);
    assert.doesNotMatch(get('next-product').textContent, /下一候选/);
    remainingTransitions = ['broker_unavailable','checkpoint_anchor_pending：等待专用窗口连接','checkpoint_guard_pending'];
    await get('collect-search').action();
    assert.equal(guardedReads, 5);
    assert.equal(get('error').textContent, '');
    assert.equal(details.length, 1);
    assert.match(get('next-product').textContent, new RegExp(prefix+'2\\b'));
  });
}
