import base64,hashlib,io,json,sqlite3,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from optimization_worker.host_session import SessionStore
from optimization_worker.collector_cli import bind_plan,import_evidence,collector_id
from optimization_worker.tab_receipt_store import TabReceiptStore
from optimization_worker.card_sales_v3_checkpoints import initialize,seal_query,append_page_batch,finish_page
from optimization_worker.card_sales_v3_pipeline import initialize_pipeline,freeze_queue,record_detail
from optimization_worker.visual_candidate import VisualCandidate
from optimization_worker.visual_v3_finalize import prepare_stage
from optimization_worker.research_return_bridge import next_gallery_step

class EndToEndTest(unittest.TestCase):
    def test_bound_result_with_only_supplemental_enters_gallery(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);buffer=io.BytesIO();Image.new('RGB',(120,120),'pink').save(buffer,format='PNG');raw=buffer.getvalue();sha=hashlib.sha256(raw).hexdigest()
            rid='01234567-89ab-4cde-8fab-0123456789ab';store=SessionStore(root/'host.sqlite','test');store.files.mkdir(exist_ok=True)
            config={'platform':'mercado-libre-mx','result_limit':5,'min_sold':500,'min_reviews':50,'min_rating':4.1,'strategy':'card-visual-v3','initial_query_count':3,'max_expansion_queries':6,'max_pages_per_query':3,'sales_known_target':100,'top_per_query':30,'visual_limit':30}
            p={'category_code':'dress','category':'vestido','subtype':'vestido','silhouette':'vuelo fluido','fit':'holgado','color':'rojo','style_codes':['elegant'],'style':['elegante'],'selling_point_codes':['round_neck','sleeveless'],'selling_points':['cuello redondo','sin mangas'],'construction':['vuelo fluido'],'defining_features':['cuello redondo','sin mangas'],'exclusions':['manga larga'],'scene_codes':['wedding'],'use_scene':['boda'],'query_language':'es-MX','query_seeds':['vestido','vestido vuelo fluido','vestido elegante']}
            primary={'category':'dress','components':'one_piece','length':'long','shoulders':'regular','structure':'one_piece','silhouette':'a_line','fit':'loose','sleeve_length':'short'}
            payload={'site_id':'MLM'};claim={'id':1,'company_id':6,'snapshot_id':5,'task_type':'research.bestseller','payload':payload,'input_hash':'hash','lease_epoch':1}
            snapshot={'case_id':4,'snapshot_id':5,'payload':{'source_image_artifact_ids':['1'],'collection_config':config}}
            store.enqueue(rid,'research.bestseller',payload,{'snapshot':snapshot,'artifacts':{'1':{'case_id':4,'company_id':6,'data':base64.b64encode(raw).decode(),'digest':sha}}})
            plan={'platform':'mercado-libre-mx','queries':p['query_seeds'],'category':'dress','result_limit':5,'source_artifact_id':'1','viewed_source_sha256':sha,'visual_profile':p,'strategy':'card-visual-v3','design_profile':{'primary':primary,'secondary':['lace'],'styles':['boho'],'occasions':[]}}
            digest=bind_plan(store,rid,claim,plan);key=(rid,digest,1);ledger=root/'ledger';receipts=TabReceiptStore(str(ledger))
            with sqlite3.connect(ledger) as db:
                initialize(db);initialize_pipeline(db)
                binding={'tab_id':3,'window_id':4,'session_id':'b'*32,'plan_digest':digest,'platform':'mercado-libre-mx'}
                db.execute('INSERT INTO bindings VALUES (?,?)',(rid,json.dumps(binding)))
                for i,q in enumerate(p['query_seeds']):
                    seal_query(db,key,i,q,fence=lambda:True)
                    if i==0:
                        card={'query':q,'product_id':'MLM1','url':'https://articulo.mercadolibre.com.mx/MLM1','rank':1,'bbox':[1,1,30,40],'is_ad':False,'sales_text':'+150 vendidos','sales_status':'known','title':'Dress','thumbnail_url':None}
                        append_page_batch(db,key,0,1,0,[card],fence=lambda:True)
                        shot=io.BytesIO();Image.new('RGB',(120,120),'white').save(shot,format='JPEG');shotraw=shot.getvalue();shotsha=hashlib.sha256(shotraw).hexdigest();shotfile=Path(store._file(shotraw)).name
                        db.execute('INSERT INTO card_v3_batch_evidence VALUES (?,?,?,?,?,?,?,?,?,?)',(*key,0,1,0,shotsha,shotfile,120,120))
                    finish_page(db,key,i,1,'end_of_results',fence=lambda:True)
                    receipt=dict(binding,request_id=rid,version=4,lease_epoch=1,query_index=i,query=q)
                    db.execute('INSERT INTO accepted_receipts(request_id,body) VALUES (?,?)',(rid,json.dumps(receipt)))
                freeze_queue(db,key,plan_exhausted=True,fence=lambda:True)
                record_detail(db,key,0,'MLM1','+150 vendidos',500,fence=lambda:True)
                detail={'identity':'mercado-libre-mx:MLM1','status':'rejected','reason':'threshold_failure','detail_url':'https://articulo.mercadolibre.com.mx/MLM1','product_id':'MLM1','title':'Dress','category':None,'visual_category_code':None,'sold_display':'+150 vendidos','reviews_display':None,'rating_display':None,'match_level':None,'visual_features':None}
                db.execute('INSERT INTO card_v3_detail_observations VALUES (?,?,?,?,?)',(*key,0,json.dumps(detail)))
            review=VisualCandidate(ledger,key,plan,config,claim);downloads=root/'Downloads';downloads.mkdir()
            broker=SimpleNamespace(watch_only=True,gallery_binding=None,conflict=False,rebind_requested=False,fence=lambda:True,anchor_guard_alive=lambda:True,guard_alive=lambda _:True,anchor_origin='anchor',tab=3,window=4,session='session')
            event={'request_id':rid,'tab_id':3,'window_id':4,'session_id':'session','native_origin':'anchor'};source='https://http2.mlstatic.com/source.png'
            ticket=review.native(broker,dict(event,operation='visual_photo_start',detail=detail,photo=1,source=source,variant='pink'),store,downloads)
            target=downloads/ticket['filename'];target.parent.mkdir(parents=True);target.write_bytes(raw)
            review.native(broker,dict(event,operation='visual_photo_saved',receipt=ticket['receipt'],download_id=1,final_url=source,size=len(raw)),store,downloads)
            review.submit({'submission_id':'card-v3-test-01','receipts':[ticket['receipt']],'candidate_primary':primary,'secondary':['lace'],'styles':['boho'],'occasions':[],'features':['lace']},store,lambda:None)
            stamp=datetime.now(timezone.utc).isoformat()
            with sqlite3.connect(ledger) as db:evidence=prepare_stage(db,key,review.scope,plan,config,collector_id(rid),stamp,lambda filename,digest:review.photo(store.files,filename,digest))
            class Client:
                research_ledger_enabled=False
                def create_artifact(self,case_id,**kwargs):
                    self.raw=kwargs['data'];return {'digest':hashlib.sha256(self.raw).hexdigest(),'artifact_id':'9'}
                def artifact(self,identity):return {'case_id':4,'company_id':6,'mime_type':'application/json','digest':hashlib.sha256(self.raw).hexdigest(),'data':base64.b64encode(self.raw).decode()}
            self.assertEqual(import_evidence(store,rid,claim,evidence,stamp,Client(),lambda:None,tab_receipts=receipts),1)
            record=store.inspect(rid);self.assertEqual(record['state'],'candidate_images_pending')
            self.assertEqual(next_gallery_step(record)['identity'],'mercado-libre-mx:MLM1')
