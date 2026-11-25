from datetime import timedelta
from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('post_install','-at_install','optimization_http')
class ScopedPreflightHTTPTest(HttpCase):
    def test_scoped_preflight_and_editor_prepare_are_bounded(self):
        batch=self.env['meli.optimization.batch'].create({'name':'scoped preflight'})
        case=self.env['meli.optimization.case'].create({'name':'scoped','batch_id':batch.id})
        snap=self.env['meli.optimization.snapshot'].create({'case_id':case.id,'payload':{}})
        task=self.env['meli.optimization.task'].create({'case_id':case.id,'snapshot_id':snap.id,'task_type':'product.facts','payload':{'schema_version':'1','product_snapshot_id':str(snap.id)}})
        task._set({'task_type':'research.bestseller'})
        def headers(login, group):
            user=self.env['res.users'].create({'name':login,'login':login,'group_ids':[(6,0,[self.env.ref(group).id])]})
            key=self.env['res.users.apikeys'].with_user(user).sudo()._generate('rpc','scoped test',fields.Datetime.now()+timedelta(hours=1))
            return {'Authorization':'Bearer '+key}
        scoped=headers('scoped-preflight-worker','meli_optimization.group_research_worker')
        editor=headers('scoped-preflight-editor','meli_optimization.group_editor')
        url='/internal/meli-optimization/scoped-preflight'
        prepare='/api/meli/optimization/tasks/%s/prepare-scoped-research' % task.id
        seal='/internal/meli-optimization/tasks/%s/research-intents/seal' % task.id
        self.assertEqual(self.url_open(seal,json={'request_id':'r'},headers=scoped).status_code,400)
        identity=self.url_open('/internal/meli-optimization/scoped-identity',json={'case_id':case.id},headers=scoped)
        self.assertEqual(identity.status_code,200,identity.text)
        self.assertEqual(identity.json()['data'],{'case_id':case.id,'scoped_worker':True,'non_admin':True,'legacy_artifact_denied':True,'new_tasks_only':True})
        def post_empty(url, headers):
            return self.url_open(url,data='{}',headers=dict(headers,**{'Content-Type':'application/json'}))
        self.assertEqual(post_empty(prepare,scoped).status_code,403)
        self.assertEqual(self.url_open(prepare,json={'unexpected':1},headers=editor).status_code,400)
        self.assertEqual(post_empty(prepare,editor).json()['data'],{'scoped':True,'coverage':'unknown'})
        result=self.url_open(url,json={'task_id':task.id},headers=scoped)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['data'],{'task_id':task.id,'scoped_worker':True,'non_admin':True,'legacy_artifact_denied':True,'fresh_task':True})
        self.assertEqual(post_empty(prepare,editor).status_code,409)
