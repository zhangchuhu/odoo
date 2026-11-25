import base64
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image
from optimization_worker.collector_progress import CollectorProgressError
from optimization_worker.domain import canonical_hash
from optimization_worker.gallery_download import GalleryDownloads,read_slot,supported_url
from optimization_worker.host_session import SessionStore
from optimization_worker.tab_receipt_store import TabReceiptStore


class GalleryDownloadTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.downloads=root/'Downloads'
        self.downloads.mkdir()
        self.store=SessionStore(root/'host.sqlite','current')
        self.request='12345678-1234-4123-8123-123456789012'
        self.identity='mercado-libre-mx:MLM123'
        self.url='https://www.mercadolibre.com.mx/dress/p/MLM123'
        self.sources=['https://http2.mlstatic.com/first.webp','https://http2.mlstatic.com/second.webp']
        self.raw=[]
        for i in (1,2):
            buf=io.BytesIO()
            Image.new('RGB',(i+1,i+1),'blue').save(buf,format='WEBP')
            self.raw.append(buf.getvalue())
        screen=self.store._file(self.raw[0])
        self.claim=dict(company_id=3,snapshot_id=4,input_hash='h',lease_epoch=6)
        plan=dict(company_id=3,snapshot_id=4,input_hash='h',request_id=self.request,case_id=10,plan={'platform':'mercado-libre-mx'})
        plan['digest']=canonical_hash(plan)
        candidate=dict(platform='mercado-libre-mx',item_id='MLM123',url=self.url)
        manifest=dict(detail_url=self.url,images=self.sources,screenshot_sha256=hashlib.sha256(self.raw[0]).hexdigest(),screenshot_path=screen)
        pending=dict(artifact_id='1611',ledger_v1=True,result={'candidates':[candidate]},gallery_manifests={self.identity:manifest})
        with self.store._db() as db:
            self.store._put(db,dict(request_id=self.request,state='candidate_images_pending',collector_plan=plan,
                                    collector_artifact_id='1611',collector_pending=pending))
        self.client=Mock(research_ledger_enabled=True)
        artifacts={}
        def upload(claim,rid,intent,data,mime_type):
            number=str(len(artifacts)+300)
            artifacts[number]=dict(case_id=10,company_id=3,mime_type=mime_type,digest=hashlib.sha256(data).hexdigest(),data=base64.b64encode(data).decode())
            return {'digest':artifacts[number]['digest'],'artifact_id':number}
        self.client.create_research_artifact.side_effect=upload
        self.client.artifact.side_effect=artifacts.__getitem__
        self.handler=GalleryDownloads(self.store,self.client,self.claim,self.downloads)
        self.broker=Mock(tab=44,window=7,session='a'*32,anchor_origin='chrome-extension://'+'b'*32+'/')
        self.broker.challenge=dict(request_id=self.request,plan_digest=plan['digest'],lease_epoch=6,platform='mercado-libre-mx')
        self.broker.store=TabReceiptStore(str(root/'receipt'))
        self.broker.gallery_binding.proven_tab=(7,'a'*32,'c'*32,44)
        self.broker.gallery_binding.confirmed=(self.identity,self.url)
        self.broker.gallery_binding.ready.return_value=True
        self.base=dict(request_id=self.request,tab_id=44,window_id=7,session_id='a'*32,identity=self.identity,native_origin=self.broker.anchor_origin)

    def next(self):
        return self.handler.handle(self.broker,dict(self.base,operation='gallery_download_next'))

    def saved(self,offer,**changed):
        index=offer['order']
        event=dict(self.base,operation='gallery_download_saved',order=index,source=self.sources[index],token=offer['token'],
                   filename=offer['filename'],download_id=index+4,final_url=self.sources[index],size=len(self.raw[index]))
        return self.handler.handle(self.broker,dict(event,**changed))

    def test_qualified_preview_reuses_first_image_only_on_explicit_request(self):
        with self.store._db() as db:
            record = self.store._get(db,self.request)
            record['collector_pending']['gallery_manifests'][self.identity]['variant'] = 'original-variant'
            self.store._put(db,record)
        self.handler.preview_reader = Mock(return_value=self.raw[0])
        self.assertEqual(self.next()['stage'],'image')
        self.handler.preview_reader.assert_not_called()
        receipt = self.handler.handle(self.broker,dict(self.base,operation='gallery_download_next',reuse_preview=True))
        self.assertEqual((receipt['stage'],receipt['order']),('reused',0))
        self.handler.preview_reader.assert_called_once_with(self.identity,self.sources[0],'original-variant')
        self.assertEqual(self.next()['order'],1)
        self.assertEqual(self.client.create_research_artifact.call_count,1)

    def test_verified_slots_upload_and_read_back_each_original_once(self):
        for i,raw in enumerate(self.raw):
            offer=self.next()
            self.assertEqual((offer['order'],offer['source']),(i,self.sources[i]))
            self.assertEqual(offer,self.next())
            self.handler=GalleryDownloads(self.store,self.client,self.claim,self.downloads)
            self.assertEqual(offer,self.next(),'persist signed filename across worker restarts')
            path=self.downloads/offer['filename']
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
            for change in (dict(token='wrong'),dict(source=self.sources[1-i]),dict(filename=str(path)),dict(order=1-i),
                           dict(size=999999),dict(final_url='https://mlstatic.com.evil.invalid/image.webp')):
                with self.assertRaises(CollectorProgressError):
                    self.saved(offer,**change)
            response=self.saved(offer)
            self.assertEqual(response['digest'],hashlib.sha256(raw).hexdigest())
            with self.assertRaises(CollectorProgressError):
                self.saved(offer)
        self.assertEqual(self.next(),dict(ok=True,identity=self.identity,stage='complete'))
        self.assertEqual(self.client.create_research_artifact.call_count,2)
        self.assertEqual(self.client.artifact.call_count,2)

    def test_reject_unknown_upload_missing_guard_symlinks_and_hardlinks(self):
        offer=self.next()
        path=self.downloads/offer['filename'];path.parent.mkdir(parents=True)
        other=self.downloads/'other';other.write_bytes(self.raw[0])
        path.symlink_to(other)
        with self.assertRaises(CollectorProgressError): self.saved(offer)
        path.unlink()
        os.link(other,path)
        with self.assertRaises(CollectorProgressError): self.saved(offer)
        path.unlink()
        path.write_bytes(self.raw[0])
        self.broker._tab_guard_connected.return_value=False
        with self.assertRaises(CollectorProgressError): self.saved(offer)
        self.broker._tab_guard_connected.return_value=True
        self.client.create_research_artifact.side_effect=OSError('lost receipt')
        with self.assertRaises(CollectorProgressError) as caught: self.saved(offer)
        self.assertEqual(caught.exception.code,'gallery_download_reconcile')
        with self.assertRaises(CollectorProgressError): self.next()
        self.assertEqual(self.client.create_research_artifact.call_count,1)

    def test_parent_symlink_is_never_followed_and_redirect_must_stay_on_platform(self):
        p=self.downloads/'elsewhere';p.mkdir()
        (p/'test.png').write_bytes(self.raw[0])
        (self.downloads/'link').symlink_to(p,target_is_directory=True)
        with self.assertRaises(OSError): read_slot(self.downloads,('link','test.png'))
        self.assertTrue(supported_url('https://img.ltwebstatic.com/path.jpg','shein-us'))
        self.assertFalse(supported_url('https://img.ltwebstatic.com/path.jpg','mercado-libre-mx'))
        self.assertFalse(supported_url('https://me@mlstatic.com/path.jpg','mercado-libre-mx'))
