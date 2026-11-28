import json
import sqlite3
import unittest

from optimization_worker.card_sales_v3_checkpoints import initialize
from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue,record_detail
from optimization_worker.home_collection_checkpoint import finish_homepage
from optimization_worker.home_suggestion_checkpoint import initialize as initialize_suggestions,save_observation,observations
from optimization_worker.home_suggestions import rank_suggestions,initial_queries


class HomeCollectionIntegrationTests(unittest.TestCase):
    def test_home_search_receipts_finish_three_empty_pages_without_next_page(self):
        import tempfile
        from datetime import datetime,timezone
        from pathlib import Path
        from unittest.mock import patch
        from optimization_worker.tab_receipt_store import TabReceiptStore
        from optimization_worker.tab_receipt_broker import Broker
        with tempfile.TemporaryDirectory() as tmp:
            ledger=Path(tmp)/'ledger';store=TabReceiptStore(str(ledger));scope=('req','a'*64,1);queries=['uno','dos','tres']
            store.card_home_state(*scope,queries,lambda:True)
            anchor='chrome-extension://'+'a'*32+'/';session='b'*32
            for index,query in enumerate(queries):
                challenge=dict(version=5,request_id='req',nonce=str(index+1)*32,lease_epoch=1,plan_digest='a'*64,platform='mercado-libre-mx',query_index=index,query=query)
                receipt=dict(challenge,session_id=session,tab_id=11,window_id=12,collector_tab_id=11,url='https://listado.mercadolibre.com.mx/'+query,observed_at=datetime.now(timezone.utc).isoformat())
                store.issue(challenge);store.accept(receipt,lambda:True)
                broker=Broker(Path(tmp)/'socket',ledger,challenge,11,lambda:True,expected_window_id=12,expected_session=session,anchor_origin=anchor,progress_plan=({'strategy':'card-home-v4'},{}))
                broker.requested_page=1
                event=dict(operation='card_v3_page_challenge',request_id='req',tab_id=11,window_id=12,session_id=session,query_index=index,query=query,page=1,native_origin=anchor)
                with patch.object(broker,'anchor_guard_alive',return_value=True):
                    token=broker.issue_card_v3_page(event);self.assertIsNotNone(token)
                    confirmation=dict(operation='card_v3_page_confirm',request_id='req',tab_id=11,window_id=12,session_id=session,receipt=dict(token,observed_at=datetime.now(timezone.utc).isoformat()),native_origin=anchor)
                    self.assertTrue(broker.confirm_card_v3_page(confirmation))
                    finish=dict(operation='card_v3_page_finish',request_id='req',tab_id=11,window_id=12,session_id=session,query_index=index,query=query,page=1,page_url=receipt['url'],status='end_of_results',next_url=None,end_checks=3,end_evidence='empty_results',native_origin=anchor)
                    self.assertTrue(broker.finish_card_v3_page(finish))
            state=store.card_home_state(*scope,queries,lambda:True)
            self.assertIsNone(state['next_page']);self.assertEqual(state['progress']['completed_pages'],[1,1,1])
            with sqlite3.connect(ledger) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM card_v3_pages WHERE page>1').fetchone()[0],0)
                freeze_queue(db,scope,plan_exhausted=True,fence=lambda:True,homepage_only=True)

    def test_observed_suggestions_flow_into_homepages_and_sales_tiers(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close);initialize(db);initialize_pipeline(db);initialize_suggestions(db)
        scope=('request','digest',1);fence=lambda:True;verify=lambda _:True
        for i,text in enumerate(('conjunto mujer falda blusa','conjunto mujer formal'),1):
            save_observation(db,scope,{'prefix':'conjunto mujer','text':text,'position':i,'observed_at':'2026-11-27T00:00:00Z','evidence_sha256':'a'*64},fence=fence,verify_evidence=verify)
        ranked=rank_suggestions('womens_skirt_set',observations(db,scope),[])
        queries=initial_queries('conjunto mujer falda',ranked,[])
        for index,item in enumerate(queries):
            query=item['text'];db.execute('insert into card_v3_queries values (?,?,?,?,?)',(*scope,index,query))
            card={'product_id':'MLM1','rank':1,'sales_status':'known','sales_text':'+100 vendidos','query':query}
            db.execute('insert into card_v3_batches values (?,?,?,?,?,?,?)',(*scope,index,1,0,json.dumps([card])))
            finish_homepage(db,scope,index,{'url':'https://listado.mercadolibre.com.mx/query','receipt_nonce':'a'*32,'end_checks':3,'no_results':False},fence=fence,verify=verify)
        self.assertEqual(freeze_queue(db,scope,plan_exhausted=True,fence=fence,homepage_only=True),['MLM1'])
        detail=record_detail(db,scope,0,'MLM1','+100 vendidos',500,fence=fence,homepage_only=True)
        self.assertEqual(detail,'supplemental')
        self.assertEqual(db.execute('select count(*) from card_v3_pages where page>1').fetchone()[0],0)
