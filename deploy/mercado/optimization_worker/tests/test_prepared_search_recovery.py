import json, sqlite3, tempfile, unittest
from pathlib import Path
from optimization_worker.domain import canonical_hash
from optimization_worker.tab_receipt_store import TabReceiptStore
from optimization_worker.prepared_search_recovery import read_prepared_search

class RecoveryTest(unittest.TestCase):
 def test_visual_v2_restores_durable_batches_without_transferring_browser_receipts(self):
  from datetime import datetime, timezone
  from unittest.mock import Mock, patch
  from optimization_worker.collector_progress import initialize_details
  from optimization_worker.visual_v2_checkpoints import initialize,seal_queries,append_batch,finish_query,read_queries,load_query
  from optimization_worker.visual_v2_outcomes import _initialize
  from optimization_worker.prepared_search_recovery import restore_search_checkpoint
  with tempfile.TemporaryDirectory() as tmp:
   ledger=TabReceiptStore(str(Path(tmp)/'ledger'))
   old,new='old','new'
   prior=dict(id=242,company_id=1,snapshot_id=2,input_hash='a'*64,payload={},lease_epoch=1,task_type='research.bestseller')
   claim=dict(prior,lease_epoch=2,lease_token='new')
   primary=dict(category='dress',components={'status':'not_applicable','value':None},length='long',shoulders='regular',structure='one_piece',silhouette='a_line',fit='fitted',sleeve_length='short')
   plan=dict(platform='mercado-libre-mx',queries=['vestido largo','vestido encaje','vestido fiesta'],category='dress',strategy='visual-v2',design_profile=dict(primary=primary,secondary=['lace'],styles=['romantic'],occasions=['party']))
   binding=dict(request_id=old,company_id=1,snapshot_id=2,input_hash=prior['input_hash'],plan=plan);binding['digest']=canonical_hash(binding)
   source=dict(request_id=old,state='prepared',task_type=prior['task_type'],payload={},collector_plan=binding)
   key=(old,binding['digest'],1); now=datetime.now(timezone.utc).isoformat()
   with sqlite3.connect(ledger.path) as db:
    initialize(db);initialize_details(db);_initialize(db);seal_queries(db,key,plan['queries'],fence=lambda:True)
    for i,q in enumerate(plan['queries']):
     receipt=dict(version=3,request_id=old,plan_digest=key[1],lease_epoch=1,query_index=i,query=q,platform=plan['platform'],tab_id=3,collector_tab_id=3,window_id=4,session_id='a'*32,observed_at=now)
     db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(old,json.dumps(receipt)))
     card=dict(query=q,rank=1,is_ad=False,title='Dress',url=f'https://articulo.mercadolibre.com.mx/MLM-{1000000000+i}-dress',product_id=f'MLM{1000000000+i}')
     append_batch(db,(*key,str(i)),0,[card],fence=lambda:True);finish_query(db,(*key,str(i)),'home_complete',fence=lambda:True)
   store=Mock();store.inspect.side_effect={old:source,new:dict(state='prepared')}.__getitem__
   server=Mock();server.request.return_value=dict(allowed=True,task_id=242,snapshot_id=2,input_hash=prior['input_hash'],request_id=new,lease_epoch=2,source_request_id=old,source_epoch=1)
   with patch('optimization_worker.collector_cli.bind_plan',return_value='new-digest'):
    self.assertTrue(restore_search_checkpoint(ledger,store,[dict(request_id=old,claim=prior,done=True)],server,new,claim,lambda:True))
   with sqlite3.connect(ledger.path) as db:
    self.assertEqual(read_queries(db,(new,'new-digest',2)),plan['queries'])
    self.assertEqual(len(load_query(db,(new,'new-digest',2,'0'))),1)
    self.assertEqual(db.execute('SELECT count(*) FROM accepted_receipts WHERE request_id=?',(new,)).fetchone()[0],0)
   self.assertEqual(ledger.next_query_index(new,'new-digest',plan['queries'],2,visual_v2=True),0)
   with sqlite3.connect(ledger.path) as db:
    row=db.execute('SELECT id,body FROM accepted_receipts WHERE request_id=? ORDER BY id LIMIT 1',(old,)).fetchone()
    invalid=json.loads(row[1]);invalid['query']='different'
    db.execute('UPDATE accepted_receipts SET body=? WHERE id=?',(json.dumps(invalid),row[0]))
   with self.assertRaises(ValueError):read_prepared_search(ledger.path,source,prior)

 def test_staged_expansion_and_details_are_verified_before_recovery(self):
  from datetime import datetime,timezone
  from optimization_worker.tests.test_collector_import import fixture
  from optimization_worker.collector_progress import initialize_details
  from optimization_worker.collector_staged_policy import initialize_expansions
  import hashlib
  data,queries=fixture();extra='dress extra'
  claim={'id':242,'company_id':1,'snapshot_id':2,'input_hash':'a'*64,'payload':{'x':1},'lease_epoch':3,'task_type':'research.bestseller'}
  rid='01234567-89ab-4cde-8fab-0123456789ab'
  plan={'platform':'shein-us','queries':queries,'category':'dress','result_limit':5,'strategy':'staged-v1'}
  binding={'request_id':rid,'company_id':1,'snapshot_id':2,'input_hash':'a'*64,'plan':plan};binding['digest']=canonical_hash(binding)
  record={'request_id':rid,'task_type':'research.bestseller','state':'prepared','payload':{'x':1},'collector_plan':binding,'context':{'snapshot':{}}}
  cards=[dict(c,query=extra,product_id=str(30+c['rank']),url=f"https://us.shein.com/item-p-{30+c['rank']}.html") for c in data['queries'][0]['observations'][:1]]
  details=data['details'][:20]+[dict(data['details'][1],identity='shein-us:31',product_id='31',detail_url='https://us.shein.com/item-p-31.html')]
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'ledger';TabReceiptStore(str(path))
   with sqlite3.connect(path) as db:
    initialize_details(db);initialize_expansions(db)
    body=json.dumps({'queries':[extra]},sort_keys=True,separators=(',',':'))
    db.execute('INSERT INTO staged_expansions VALUES (?,?,?,?,?)',(rid,binding['digest'],3,body,hashlib.sha256(body.encode()).hexdigest()))
    db.execute('INSERT INTO query_card_meta VALUES (?,?,?,?,?,?)',(rid,binding['digest'],3,3,extra,1))
    for i,block in enumerate(data['queries']+[{'query':extra,'observations':cards}]):
     receipt=dict(version=2,request_id=rid,plan_digest=binding['digest'],lease_epoch=3,query_index=i,query=block['query'],platform='shein-us',tab_id=1,collector_tab_id=1,window_id=2,session_id='a'*32,observed_at=datetime.now(timezone.utc).isoformat())
     db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(rid,json.dumps(receipt)))
     db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(rid,binding['digest'],3,i,json.dumps(block['observations'])))
    for i,detail in enumerate(details):db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(rid,binding['digest'],3,i,json.dumps(detail)))
   result=read_prepared_search(str(path),record,claim)
   self.assertEqual(result['expansion_queries'],[extra]);self.assertEqual(len(result['details']),21);self.assertTrue(result['exhausted_groups'][3])
   with sqlite3.connect(path) as db:db.execute("UPDATE staged_expansions SET body='{}'")
   with self.assertRaises(ValueError):read_prepared_search(str(path),record,claim)
 def test_three_groups_read_only_and_tamper_rejected(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'ledger'; TabReceiptStore(str(path)); claim={'id':242,'company_id':1,'snapshot_id':2,'input_hash':'a'*64,'payload':{'x':1},'lease_epoch':3,'task_type':'research.bestseller'}
   plan={'request_id':'r','company_id':1,'snapshot_id':2,'input_hash':'a'*64,'plan':{'platform':'mercado-libre-mx','queries':['a','b','c']}}
   plan['digest']=canonical_hash(plan); record={'request_id':'r','task_type':'research.bestseller','state':'prepared','payload':{'x':1},'collector_plan':plan}
   with sqlite3.connect(path) as db:
    for i,q in enumerate(['a','b','c']):
     receipt={'request_id':'r','plan_digest':plan['digest'],'lease_epoch':3,'query_index':i,'query':q,'platform':'mercado-libre-mx','tab_id':1,'collector_tab_id':1,'window_id':2,'session_id':'a'*32}
     cards=[{'query':q,'rank':n+1,'is_ad':False,'title':'x','url':f'https://articulo.mercadolibre.com.mx/MLM-{1000000000+n}-x','product_id':f'MLM{1000000000+n}'} for n in range(30)]
     db.execute('insert into accepted_receipts(request_id,body) values (?,?)',('r',json.dumps(receipt)))
     db.execute('insert into query_cards values (?,?,?,?,?)',('r',plan['digest'],3,i,json.dumps(cards)))
   result=read_prepared_search(str(path),record,claim); self.assertEqual([len(x) for x in result['cards']],[30,30,30]); self.assertEqual(result['original_epoch'],3)
   with sqlite3.connect(path) as db:db.execute('delete from query_cards where query_index=1')
   with self.assertRaises(ValueError):read_prepared_search(str(path),record,claim)

 def test_retired_interrupted_request_can_read_contiguous_partial_progress(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'ledger'; TabReceiptStore(str(path)); claim={'id':242,'company_id':1,'snapshot_id':2,'input_hash':'a'*64,'payload':{'x':1},'lease_epoch':3,'task_type':'research.bestseller'}
   plan={'request_id':'r','company_id':1,'snapshot_id':2,'input_hash':'a'*64,'plan':{'platform':'mercado-libre-mx','queries':['a','b','c']}}
   plan['digest']=canonical_hash(plan)
   record={'request_id':'r','task_type':'research.bestseller','state':'unknown','resume_state':'prepared','retired':True,'retirement_reason':'original_worker_lease_inactive','payload':{'x':1},'collector_plan':plan}
   with sqlite3.connect(path) as db:
    receipt={'request_id':'r','plan_digest':plan['digest'],'lease_epoch':3,'query_index':0,'query':'a','platform':'mercado-libre-mx','tab_id':1,'collector_tab_id':1,'window_id':2,'session_id':'a'*32}
    cards=[{'query':'a','rank':n+1,'is_ad':False,'title':'x','url':f'https://articulo.mercadolibre.com.mx/MLM-{1000000000+n}-x','product_id':f'MLM{1000000000+n}'} for n in range(30)]
    db.execute('insert into accepted_receipts(request_id,body) values (?,?)',('r',json.dumps(receipt)))
    db.execute('insert into query_cards values (?,?,?,?,?)',('r',plan['digest'],3,0,json.dumps(cards)))
   result=read_prepared_search(str(path),record,claim)
   self.assertEqual(len(result['cards']),1)
   self.assertTrue(result['requires_new_browser_binding'])
   with self.assertRaises(ValueError):read_prepared_search(str(path),dict(record,collector_pending={'artifact_id':'1'}),claim)
   paused=dict(record,state='waiting',resume_state='candidate_images_pending',reason='captcha_required',
               collector_pending={'artifact_id':'1'})
   result=read_prepared_search(str(path),paused,claim)
   self.assertEqual(len(result['cards']),1)
   self.assertNotIn('collector_pending',result)


class LeaseMigrationTests(unittest.TestCase):
    def test_detail_moves_only_after_exact_cards_are_accepted_on_new_request(self):
        from contextlib import closing
        from datetime import datetime, timedelta, timezone
        from unittest.mock import Mock, patch
        from optimization_worker.collector_progress import initialize_details
        from optimization_worker.tests.test_collector_import import fixture
        from optimization_worker.prepared_search_recovery import restore_search_checkpoint
        data, queries = fixture()
        old, new = '01234567-89ab-4cde-8fab-0123456789ab', 'f1234567-89ab-4cde-8fab-0123456789ab'
        plan = {'platform': 'shein-us', 'category':'dress', 'result_limit':5, 'queries':queries}
        prior = dict(id=240,company_id=2,snapshot_id=257,input_hash='a'*64,task_type='research.bestseller',payload={},lease_epoch=9)
        claim = dict(prior,lease_epoch=10,lease_token='new')
        binding = dict(request_id=old,company_id=2,snapshot_id=257,input_hash=prior['input_hash'],plan=plan)
        binding['digest'] = canonical_hash(binding)
        source = dict(request_id=old,state='unknown',reason='unknown',resume_state='prepared',retired=True,
                      retirement_reason='original_worker_lease_inactive',task_type=prior['task_type'],
                      context={'snapshot':{}},payload={},collector_plan=binding)
        def receipts(request, digest, epoch):
            for i, query in enumerate(queries):
                yield dict(request_id=request,plan_digest=digest,lease_epoch=epoch,platform='shein-us',
                           tab_id=11,window_id=7,collector_tab_id=11,session_id='a'*32,query=query,
                           query_index=i,observed_at=datetime.now(timezone.utc).isoformat())
        for change in ('none','different_cards','stale_detail','wrong_identity','expired_cards'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                ledger=TabReceiptStore(str(Path(tmp)/'ledger.sqlite'))
                with closing(sqlite3.connect(ledger.path)) as db, db:
                    initialize_details(db)
                    detail=data['details'][0].copy()
                    if change=='stale_detail':
                        detail['observed_at']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
                    if change=='wrong_identity':
                        detail['identity']='shein-us:999'
                    db.execute('INSERT INTO collector_details VALUES (?,?,?,?,?)',(old,binding['digest'],9,0,json.dumps(detail)))
                    for i, receipt in enumerate(receipts(old,binding['digest'],9)):
                        db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(old,json.dumps(receipt)))
                        db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',(old,binding['digest'],9,i,json.dumps(data['queries'][i]['observations'])))
                store=Mock()
                store.inspect.side_effect={old:source,new:{'request_id':new,'state':'prepared'}}.__getitem__
                server=Mock()
                server.request.return_value=dict(allowed=True,task_id=240,snapshot_id=257,input_hash=prior['input_hash'],
                    request_id=new,lease_epoch=10,source_request_id=old,source_epoch=9)
                with patch('optimization_worker.collector_cli.bind_plan',return_value='new-plan'):
                    self.assertTrue(restore_search_checkpoint(ledger,store,[dict(request_id=old,claim=prior,done=True)],
                                                             server,new,claim,lambda:True))
                if change=='expired_cards':
                    with closing(sqlite3.connect(ledger.path)) as db, db:
                        db.execute('UPDATE inherited_query_cards SET observed_at=?',
                                   ((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),))
                for i, receipt in enumerate(receipts(new,'new-plan',10)):
                    with closing(sqlite3.connect(ledger.path)) as db, db:
                        self.assertEqual(db.execute('SELECT count(*) FROM collector_details WHERE request_id=?',(new,)).fetchone(),(0,))
                        db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(new,json.dumps(receipt)))
                    cards = list(data['queries'][i]['observations'])
                    if change=='different_cards' and i==0:
                        cards[0]=dict(cards[0],title='New observed title')
                    ledger.record_cards(new,'new-plan',10,i,queries[i],cards,lambda:True)
                with closing(sqlite3.connect(ledger.path)) as db:
                    rows=db.execute('SELECT body FROM collector_details WHERE request_id=?',(new,)).fetchall()
                    self.assertEqual([json.loads(r[0]) for r in rows],[detail] if change=='none' else [])
                status = ledger.detail_status(new, 'new-plan', 10)
                self.assertEqual(status['restored'], len(rows))
                self.assertEqual(status['source_request'], old if rows else None)
                self.assertEqual(status['last_identity'], detail['identity'] if rows else None)

    def test_server_authorization_allows_cards_but_never_moves_browser_receipts(self):
        from contextlib import closing
        from datetime import datetime, timezone
        from unittest.mock import Mock, patch
        from optimization_worker.prepared_search_recovery import restore_search_checkpoint

        with tempfile.TemporaryDirectory() as tmp:
            ledger = TabReceiptStore(str(Path(tmp) / 'ledger'))
            prior = dict(id=242, company_id=1, snapshot_id=257, input_hash='a'*64,
                         task_type='research.bestseller', payload={}, lease_epoch=1)
            current = dict(prior, lease_epoch=2, lease_token='new-token')
            plan = {'platform':'mercado-libre-mx','queries':['first','second','third']}
            binding = {k: prior[k] for k in ('company_id','snapshot_id','input_hash')}
            binding.update(request_id='old', plan=plan)
            binding['digest'] = canonical_hash(binding)
            source = dict(request_id='old', state='prepared', retired=True,
                          retirement_reason='original_worker_lease_inactive',
                          task_type=prior['task_type'], payload={}, collector_plan=binding)
            target = dict(request_id='new', state='prepared')
            store = Mock()
            store.inspect.side_effect = {'old': source,'new': target}.__getitem__
            server = Mock()
            run = {'claim': prior, 'request_id':'old','done':True}
            timestamp = datetime.now(timezone.utc).isoformat()
            receipt = dict(request_id='old',plan_digest=binding['digest'],lease_epoch=1,query_index=0,
                           query='first',platform=plan['platform'],tab_id=3,collector_tab_id=3,
                           window_id=4,session_id='a'*32,observed_at=timestamp)
            cards = [dict(query='first', rank=i+1,is_ad=False,title='Garment',
                          url=f'https://articulo.mercadolibre.com.mx/MLM-{1000000000+i}-x',
                          product_id=f'MLM{1000000000+i}') for i in range(30)]
            with closing(sqlite3.connect(ledger.path)) as db, db:
                db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',('old',json.dumps(receipt)))
                db.execute('INSERT INTO query_cards VALUES (?,?,?,?,?)',('old',binding['digest'],1,0,json.dumps(cards)))
            def restore():
                return restore_search_checkpoint(ledger, store, [run], server, 'new', current, lambda: None)
            with patch('optimization_worker.collector_cli.bind_plan',return_value='new-plan') as bind:
                server.request.return_value = {'allowed': False}
                self.assertFalse(restore())
                bind.assert_not_called()
                server.request.return_value = dict(allowed=True,task_id=242,snapshot_id=257,input_hash=prior['input_hash'],
                                                   request_id='new',lease_epoch=2,source_request_id='old',source_epoch=1)
                self.assertTrue(restore())
                self.assertEqual(ledger.read_inherited_cards('new','new-plan',2,0),cards)
                self.assertIsNone(ledger.current_binding('new'))
                self.assertEqual(ledger.completed_card_index('new','new-plan',2),0)
                self.assertFalse(ledger.verified_queries('new','new-plan',plan['queries'],2))
                self.assertIsNone(ledger.read_inherited_cards('new','new-plan',3,0))
                self.assertTrue(restore())
