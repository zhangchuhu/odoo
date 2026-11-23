from datetime import timedelta
from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

@tagged('post_install', '-at_install')
class CallbackProbeTest(TransactionCase):
    def test_roundtrip_contains_only_digest_and_deletes_own_probe(self):
        probe=self.env['meli.optimization.callback.probe']
        nonce='A'*32
        created=probe.probe_create(nonce)
        self.assertNotIn(nonce,str(created))
        self.assertEqual(probe.probe_read(created['probe_id'],nonce)['digest'],created['digest'])
        self.assertTrue(probe.probe_delete(created['probe_id'],nonce))
        with self.assertRaises(ValidationError): probe.probe_read(created['probe_id'],nonce)

    def test_wrong_nonce_company_and_expiry_are_rejected(self):
        probe=self.env['meli.optimization.callback.probe'];nonce='A'*32
        created=probe.probe_create(nonce)
        with self.assertRaises(ValidationError): probe.probe_read(created['probe_id'],'B'*32)
        other=self.env['res.company'].create({'name':'probe isolation'})
        self.env.user.company_ids |= other
        with self.assertRaises(ValidationError): probe.with_company(other).probe_read(created['probe_id'],nonce)
        entry=probe.sudo().search([('probe_id','=',created['probe_id'])])
        entry.write({'expires_at':fields.Datetime.now()-timedelta(seconds=1)})
        with self.assertRaises(ValidationError): probe.probe_read(created['probe_id'],nonce)
        with self.assertRaises(ValidationError): probe.probe_create('short')

from odoo.tests import HttpCase

@tagged('post_install', '-at_install', 'optimization_http')
class CallbackProbeHTTPTest(HttpCase):
    def test_authenticated_roundtrip_and_scope(self):
        group=self.env.ref('meli_optimization.group_worker').id
        def headers(login,groups):
            user=self.env['res.users'].create({'name':login,'login':login,'group_ids':[(6,0,groups)]})
            key=self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc','probe',fields.Datetime.now()+timedelta(hours=1))
            return {'Authorization':'Bearer '+key}
        worker=headers('callback-probe-worker',[group]);other=headers('callback-probe-other',[group])
        plain=headers('callback-probe-plain',[self.env.ref('base.group_user').id])
        prefix='/internal/meli-optimization/callback-probe/'
        nonce='R'*32
        self.assertEqual(self.url_open(prefix+'create',json={'nonce':nonce}).status_code,401)
        self.assertEqual(self.url_open(prefix+'create',json={'nonce':nonce},headers=plain).status_code,403)
        created=self.url_open(prefix+'create',json={'nonce':nonce},headers=worker)
        self.assertEqual(created.status_code,200)
        self.assertNotIn(nonce,created.text)
        value=created.json()['data'];request={'probe_id':value['probe_id'],'nonce':nonce}
        self.assertEqual(self.url_open(prefix+'read',json=request,headers=other).status_code,409)
        self.assertEqual(self.url_open(prefix+'read',json=dict(request,nonce='X'*32),headers=worker).status_code,409)
        read=self.url_open(prefix+'read',json=request,headers=worker)
        self.assertEqual(read.json()['data'],value)
        self.assertNotIn(nonce,read.text)
        self.assertTrue(self.url_open(prefix+'delete',json=request,headers=worker).json()['data']['deleted'])
        self.assertEqual(self.url_open(prefix+'read',json=request,headers=worker).status_code,409)

@tagged('post_install', '-at_install', 'optimization_http')
class ArtifactRollbackProbeHTTPTest(HttpCase):
    def test_authorized_artifact_probe_rolls_back_and_denies_ordinary_user(self):
        batch=self.env['meli.optimization.batch'].create({'name':'Rollback probe test'})
        case=self.env['meli.optimization.case'].create({'name':'Probe test','batch_id':batch.id})
        def headers(login,groups):
            user=self.env['res.users'].create({'name':login,'login':login,'group_ids':[(6,0,groups)]})
            key=self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc','artifact probe',fields.Datetime.now()+timedelta(hours=1))
            return {'Authorization':'Bearer '+key}
        worker=headers('artifact-probe-worker',[self.env.ref('meli_optimization.group_worker').id])
        scoped=headers('artifact-probe-scoped',[self.env.ref('meli_optimization.group_research_worker').id])
        ordinary=headers('artifact-probe-ordinary',[self.env.ref('base.group_user').id])
        url='/internal/meli-optimization/callback-probe/artifact-rollback'
        count=self.env['meli.optimization.artifact'].search_count([('case_id','=',case.id)])
        self.assertEqual(self.url_open(url,json={'case_id':case.id},headers=ordinary).status_code,403)
        self.assertEqual(self.url_open(url,json={'case_id':999999999},headers=worker).status_code,404)
        other=self.env['res.company'].create({'name':'Artifact probe isolated company'})
        foreign_batch=self.env['meli.optimization.batch'].with_company(other).create({'name':'Foreign probe'})
        foreign_case=self.env['meli.optimization.case'].with_company(other).create({'name':'Foreign probe','batch_id':foreign_batch.id})
        self.assertEqual(self.url_open(url,json={'case_id':foreign_case.id},headers=worker).status_code,403)
        response=self.url_open(url,json={'case_id':case.id},headers=worker)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['data'],{'artifact_orm_write_read_verified':True,'task_completion_verified':False})
        scoped_response=self.url_open(url,json={'case_id':case.id},headers=scoped)
        self.assertEqual(scoped_response.status_code,200,scoped_response.text)
        self.env.invalidate_all()
        self.assertEqual(self.env['meli.optimization.artifact'].search_count([('case_id','=',case.id)]),count)
        from unittest.mock import patch
        model_class=type(self.env['meli.optimization.artifact'])
        original=model_class.create
        def reject_binary(model,values):
            if isinstance(values,dict) and values.get('data'):
                raise AccessError('Injected binary write denial')
            return original(model,values)
        with patch.object(model_class,'create',reject_binary):
            denied=self.url_open(url,json={'case_id':case.id},headers=worker)
        self.assertEqual(denied.status_code,403)
        self.env.invalidate_all()
        self.assertEqual(self.env['meli.optimization.artifact'].search_count([('case_id','=',case.id)]),count)
