import unittest

from optimization_worker.collector_staged_policy import staged_order, stage_allowances, next_action


class StagedPolicyTest(unittest.TestCase):
    def test_elapsed_time_uses_verified_start_and_rejects_future_start(self):
        import sqlite3,json
        from datetime import datetime,timezone,timedelta
        from optimization_worker.collector_staged_policy import elapsed_seconds
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE accepted_receipts (request_id TEXT, body TEXT, id INTEGER)')
        now=datetime.now(timezone.utc);key=('request','a'*64,1)
        receipt=dict(plan_digest=key[1],lease_epoch=1,query_index=0,observed_at=(now-timedelta(minutes=61)).isoformat())
        db.execute('INSERT INTO accepted_receipts VALUES (?,?,?)',(key[0],json.dumps(receipt),1))
        self.assertEqual(elapsed_seconds(db,key,now),3660)
        receipt['observed_at']=(now+timedelta(seconds=1)).isoformat()
        db.execute('UPDATE accepted_receipts SET body=?',(json.dumps(receipt),))
        with self.assertRaises(ValueError):elapsed_seconds(db,key,now)
    def test_existing_candidates_are_ordered_without_duplicates(self):
        pool = {
            'a': {'hits': {0, 1}, 'organic': 8, 'ad': None, 'card': {'title': 'dress'}},
            'b': {'hits': {0}, 'organic': 1, 'ad': None, 'card': {'title': 'dress'}},
            'c': {'hits': {1}, 'organic': None, 'ad': 1, 'card': {'title': 'dress'}},
            'd': {'hits': {0, 1, 2}, 'organic': 9, 'ad': None, 'card': {'title': 'dress'}},
        }
        self.assertEqual(staged_order(pool), (['d', 'a'], ['b', 'c']))

    def test_unused_budget_flows_to_next_stage_only(self):
        self.assertEqual(stage_allowances(4, 30), (20, 36, 26))
        self.assertEqual(stage_allowances(20, 20), (20, 20, 20))
        with self.assertRaises(ValueError): stage_allowances(21, 0)

    def test_budget_rejects_invalid_counts(self):
        for values in [(-1, 0), (True, 0), (0, -1), (0, 41)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                stage_allowances(*values)

    def test_time_budget_stops_new_actions_only_after_valid_checkpoint(self):
        initial={'a': {'hits': {0}, 'organic': 1, 'ad': None, 'card': {'title': 'dress'}}}
        self.assertEqual(next_action(initial,[],0,3,None,elapsed_seconds=3600),
                         {'stage':'finished','action':'finish','reason':'time_budget_exhausted'})
        with self.assertRaises(ValueError):next_action(initial,[('single','wrong')],0,3,None,elapsed_seconds=3600)
        with self.assertRaises(ValueError):next_action(initial,[],0,3,None,elapsed_seconds=-1)

    def test_stages_resume_and_expand_only_after_initial_review(self):
        initial = {'a': {'hits': {0, 1}, 'organic': 1, 'ad': None, 'card': {'title': 'dress'}},
                   'b': {'hits': {1}, 'organic': 2, 'ad': None, 'card': {'title': 'dress'}}}
        action = next_action(initial, [], 0, 2, None)
        self.assertEqual(action, {'stage': 'repeated', 'action': 'review', 'identity': 'a'})
        action = next_action(initial, [('repeated', 'a')], 0, 2, None)
        self.assertEqual(action, {'stage': 'single', 'action': 'review', 'identity': 'b'})
        action = next_action(initial, [('repeated', 'a'), ('single', 'b')], 0, 2, None)
        self.assertEqual(action, {'stage': 'expanded', 'action': 'search'})
        action = next_action(initial, [('repeated', 'a'), ('single', 'b')], 2, 2, None)
        self.assertEqual(action, {'stage': 'finished', 'action': 'finish', 'reason': 'target_reached'})

    def test_expanded_candidates_exclude_previously_reviewed(self):
        initial = {'a': {'hits': {0}, 'organic': 1, 'ad': None, 'card': {'title': 'dress'}}}
        expanded = {'a': {'hits': {0}, 'organic': 1, 'ad': None, 'card': {'title': 'dress'}},
                    'b': {'hits': {0}, 'organic': 2, 'ad': None, 'card': {'title': 'dress'}}}
        self.assertEqual(next_action(initial, [('single', 'a')], 0, 2, expanded),
                         {'stage': 'expanded', 'action': 'review', 'identity': 'b'})
        self.assertEqual(next_action(initial, [('single', 'a'), ('expanded', 'b')], 0, 2, expanded),
                         {'stage': 'finished', 'action': 'finish', 'reason': 'candidates_exhausted'})

    def test_forged_checkpoints_cannot_complete_a_task(self):
        initial = {'a': {'hits': {0}, 'organic': 1, 'ad': None, 'card': {'title': 'dress'}}}
        with self.assertRaises(ValueError): next_action(initial, [('single', 'a'), ('expanded', 'invented')], 2, 2, {})

    def test_staged_evidence_cannot_finish_before_expansion(self):
        import copy
        from optimization_worker.collector_import import convert_staged_evidence
        from optimization_worker.tests.test_collector_import import fixture
        data, queries = fixture()
        for detail in data['details']:
            detail.update(status='rejected', reason='threshold_failure', reviews_display='3',
                          match_level=None, visual_features=None)
        data['details'] = data['details'][:20]
        with self.assertRaisesRegex(ValueError, 'expansion'):
            convert_staged_evidence(data, data['task_record_id'], 'shein-us', queries, [], 'dress',
                                    '2026-11-16T00:00:00Z', result_limit=2)
        timeout=convert_staged_evidence(data,data['task_record_id'],'shein-us',queries,[],'dress',
                                        '2026-11-16T00:00:00Z',result_limit=2,elapsed_seconds=3600)
        self.assertEqual(timeout['reason'],'time_budget_exhausted')

        extra_queries=['dress four','dress five','dress six']
        for q in extra_queries:
            cards=[]
            for rank in range(1,31):
                n=rank+30
                cards.append({'query':q,'rank':rank,'is_ad':False,'title':'Dress',
                              'url':f'https://us.shein.com/item-p-{n}.html','product_id':str(n),'thumbnail_url':None})
            data['queries'].append({'query':q,'observations':cards})
        for n in range(31,61):
            detail=copy.deepcopy(data['details'][0]);detail.update(identity=f'shein-us:{n}',product_id=str(n),
                        detail_url=f'https://us.shein.com/item-p-{n}.html')
            data['details'].append(detail)
        result=convert_staged_evidence(data,data['task_record_id'],'shein-us',queries,extra_queries,'dress',
                                      '2026-11-16T00:00:00Z',result_limit=2)
        self.assertEqual(result['status'],'empty')
        self.assertEqual(result['candidates'],[])
        self.assertEqual(result['reason'],'candidates_exhausted')
        data['details'][20].update(status='qualified',reason=None,reviews_display='500+',
                                    rating_display='4.8',match_level='类似竞品',visual_features=['round neckline'])
        result=convert_staged_evidence(data,data['task_record_id'],'shein-us',queries,extra_queries,'dress',
                                      '2026-11-16T00:00:00Z',result_limit=2)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['reason'],'candidates_exhausted')
        data['details'][20]['reviews_display']='3'
        with self.assertRaises(ValueError):
            convert_staged_evidence(data,data['task_record_id'],'shein-us',queries,extra_queries,'dress',
                                    '2026-11-16T00:00:00Z',result_limit=2)
        data['details'][20].update(status='rejected',reason='threshold_failure',match_level=None,visual_features=None)
        bad=copy.deepcopy(data);bad['queries'][3]['query']='red dress'
        for card in bad['queries'][3]['observations']:card['query']='red dress'
        with self.assertRaisesRegex(ValueError, 'queries'):
            convert_staged_evidence(bad,bad['task_record_id'],'shein-us',queries,['red dress','dress five','dress six'],'dress',
                                    '2026-11-16T00:00:00Z',result_limit=2)
        limited=copy.deepcopy(data);limited['queries']=limited['queries'][:4]
        result=convert_staged_evidence(limited,limited['task_record_id'],'shein-us',queries,extra_queries[:1], 'dress',
                                       '2026-11-16T00:00:00Z',result_limit=2)
        self.assertEqual(result['status'],'empty')

    def test_mercado_staged_result_requires_verifiable_sales(self):
        from optimization_worker.collector_import import convert_staged_evidence
        from optimization_worker.tests.test_collector_import import fixture
        data,queries=fixture(); data['platform']='mercado-libre-mx'
        for block in data['queries']:
            for card in block['observations']:
                card['product_id']='MLM'+card['product_id']
                card['url']='https://articulo.mercadolibre.com.mx/'+card['product_id']
        detail=data['details'][0];detail.update(identity='mercado-libre-mx:MLM1',product_id='MLM1',
                 detail_url='https://articulo.mercadolibre.com.mx/MLM1',sold_display='+500 vendidos')
        data['details']=[detail]
        result=convert_staged_evidence(data,data['task_record_id'],data['platform'],queries,[], 'dress',
                                       '2026-11-16T00:00:00Z',result_limit=1,min_sold=500)
        self.assertEqual(result['candidates'][0]['sold_lower_bound'],500)
        for invalid in ('+100 vendidos', None):
            detail['sold_display']=invalid
            with self.assertRaises(ValueError):
                convert_staged_evidence(data,data['task_record_id'],data['platform'],queries,[], 'dress',
                                        '2026-11-16T00:00:00Z',result_limit=1,min_sold=500)

    def test_staged_prefix_requires_in_order_valid_sales_evidence(self):
        from optimization_worker.collector_import import validate_staged_evidence_prefix
        from optimization_worker.tests.test_collector_import import fixture
        data,queries=fixture();data['details']=data['details'][:1]
        validate_staged_evidence_prefix(data,data['task_record_id'],'shein-us',queries,[], 'dress',
                                        '2026-11-16T00:00:00Z',result_limit=2)
        data['details'][0]['product_id']='9'
        with self.assertRaises(ValueError):
            validate_staged_evidence_prefix(data,data['task_record_id'],'shein-us',queries,[], 'dress',
                                            '2026-11-16T00:00:00Z',result_limit=2)

    def test_expansion_is_rejected_when_initial_phase_reached_target(self):
        from optimization_worker.collector_import import convert_staged_evidence
        from optimization_worker.tests.test_collector_import import fixture
        data,queries=fixture();data['details']=data['details'][:1]
        q='extra dress';cards=[dict(c,query=q) for c in data['queries'][0]['observations']]
        data['queries'].append({'query':q,'observations':cards})
        with self.assertRaisesRegex(ValueError,'expansion'):
            convert_staged_evidence(data,data['task_record_id'],'shein-us',queries,[q], 'dress',
                                    '2026-11-16T00:00:00Z',result_limit=1)

    def test_expansion_checkpoint_is_immutable_and_lease_fenced(self):
        import sqlite3
        from optimization_worker.collector_staged_policy import save_expansion, load_expansion, inherit_expansion, initialize_expansions
        db=sqlite3.connect(':memory:');initialize_expansions(db)
        initial={'a': {'hits': {0}, 'organic': 1, 'ad': None, 'card': {'title': 'dress'}}}
        key=('request', 'a'*64, 1)
        args=(db,key,initial,[('single','a')],0,2,['extra dress'],['dress one','dress two','dress three'])
        with self.assertRaises(ValueError):save_expansion(*args,fence=lambda:False)
        first=save_expansion(*args,fence=lambda:True)
        self.assertEqual(first,save_expansion(*args,fence=lambda:True))
        self.assertEqual(load_expansion(db,key,fence=lambda:True), {'queries':['extra dress']})
        with self.assertRaises(ValueError):load_expansion(db,key,fence=lambda:False)
        nextkey=('request','a'*64,2)
        with self.assertRaises(ValueError):inherit_expansion(db,key,nextkey,fence=lambda:True,reconcile=lambda:False)
        self.assertEqual(inherit_expansion(db,key,nextkey,fence=lambda:True,reconcile=lambda:True),first)
        self.assertEqual(load_expansion(db,nextkey,fence=lambda:True), {'queries':['extra dress']})
        other=(db,key,initial,[('single','a')],0,2,['another dress'],['dress one','dress two','dress three'])
        with self.assertRaises(ValueError):save_expansion(*other,fence=lambda:True)
        db.execute("UPDATE staged_expansions SET body='{}'")
        with self.assertRaises(ValueError):load_expansion(db,key,fence=lambda:True)
        db.close()
