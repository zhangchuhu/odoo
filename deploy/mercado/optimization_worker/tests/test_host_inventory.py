import copy
from test_host_session import HostSessionTests, caps


class InventoryTests(HostSessionTests):
    def begin(self, covered=False):
        self.store.enqueue('inv', 'image.generate', self.payload, self.context)
        action = self.store.next()
        plan = {'selected_reference_ids':['2'], 'target_color':'red', 'garment_facts':{'required':['garment_type:dress'],'forbidden':[]},
                'classification':'infographic', 'source_evidence':[{'source_id':'2','angle':'front','roles':['model'],'information_score':90,'observed_color':'red'}],
                'garment_instances':['dress'], 'viewed_digests':action['input_digests']}
        if covered: plan['source_evidence'][0]['roles'].append('instance:dress detail')
        self.store.plan('inv', plan)
        return plan

    def envelope(self, action, contents=None):
        return {'schema_version':1, 'request_id':'inv', 'target_digest':action['target_digest'],
                'reading_round':action['reading_round'], 'inventory':contents or {'visible_text':['SALE!'], 'panels':['front'], 'garment_instances':['dress detail']}}

    def test_agreement_reopens_target_and_requires_coverage_before_dispatch(self):
        plan=self.begin(); a=self.store.next(); self.assertEqual(a['action'],'read_inventory')
        self.assertFalse(self.store.dispatch('inv',caps(),{})['should_invoke'])
        self.store.inventory('inv',self.envelope(a)); a=self.store.next()
        self.assertEqual(a['reading_round'],2); self.assertNotIn('readings',a)
        self.store.inventory('inv',self.envelope(a)); a=self.store.next()
        self.assertEqual(a['action'],'select_references'); self.assertEqual(a['inventory']['visible_text'],['SALE!'])
        plan['source_evidence'][0]['roles'].append('instance:dress detail')
        self.store.plan('inv',plan); a=self.store.next(); self.assertEqual(a['action'],'generate')
        self.assertIn('SALE!',self.store.inspect('inv')['invocation']['prompt'])

    def test_covered_tentative_plan_continues_after_agreement(self):
        self.begin(covered=True)
        a=self.store.next(); self.store.inventory('inv',self.envelope(a))
        a=self.store.next(); self.store.inventory('inv',self.envelope(a))
        self.assertEqual(self.store.next()['action'],'generate')

    def test_verified_capacity_pauses_without_dispatch(self):
        self.test_covered_tentative_plan_continues_after_agreement()
        a=self.store.next(); c=caps(); c['max_input_images']=1
        self.assertFalse(self.store.dispatch('inv',c,a['input_digests'])['should_invoke'])
        self.assertEqual(self.store.next()['reason'],'capacity_insufficient')

    def test_disagreement_allows_only_one_unconfirmed_adjudication(self):
        self.begin(); a=self.store.next(); self.store.inventory('inv',self.envelope(a))
        a=self.store.next(); other=self.envelope(a); other['inventory']['visible_text']=['different']; self.store.inventory('inv',other)
        a=self.store.next(); self.assertEqual(a['action'],'adjudicate_inventory'); x=self.envelope(a); x['confirmed']=False
        self.store.inventory('inv',x); a=self.store.next(); self.assertEqual(a['reason'],'inventory_unsettled')
        self.assertFalse(self.store.dispatch('inv',caps(),{})['should_invoke'])
        with self.assertRaises(ValueError): self.store.inventory('inv',x)

    def test_invalid_selection_fixes_are_bounded(self):
        plan=self.begin(); a=self.store.next(); self.store.inventory('inv',self.envelope(a)); a=self.store.next(); self.store.inventory('inv',self.envelope(a))
        plan['selected_reference_ids']=['unknown']
        for _ in range(3):
            with self.assertRaises(ValueError): self.store.plan('inv',plan)
        self.assertEqual(self.store.next()['reason'],'selection_submission_exhausted')

    def test_infographic_qc_requires_exact_visual_fields(self):
        self.test_covered_tentative_plan_continues_after_agreement()
        a=self.store.next(); dispatched=self.store.dispatch('inv',caps(),a['input_digests'])
        with self.store._db() as db:
            r=self.store._get(db,'inv'); path=r['ordered_image_paths'][0]
            r.update(state='qc_pending',candidate_path=path,uploads={'candidate':{'sha256':r['generation_digests'][path],'path':path}})
            self.store._put(db,r)
        a=self.store.next(); self.assertEqual(a['inventory']['visible_text'],['SALE!'])
        report={'viewed_digests':a['input_digests'], 'quality_binding':a['quality_binding'], 'review_binding':a['review_binding'],
                'checks':[{'name':n,'status':'passed','evidence':'synthetic fixture'} for n in a['required_checks']],
                'skill_report':{'schema_version':1,'candidate':'candidate_1','scores':{'garment_construction':90,'color_material':88,'garment_details':88,'target_preservation':90,'text_layout':None},'critical_defects':[],'primary_defect':None,'evidence':['synthetic fixture'],'confidence':0.85,'decision':'accept','exact_text':None,'added_text':None,'missing_text':None,'instances_exact':None,'panel_count_exact':None,'panel_layout_exact':None}}
        with self.assertRaises(ValueError): self.store.qc('inv',report)
        self.assertIsNone(self.store.result('inv'))

    def test_infographic_qc_accepts_complete_synthetic_report(self):
        self.test_covered_tentative_plan_continues_after_agreement()
        a=self.store.next(); self.store.dispatch('inv',caps(),a['input_digests'])
        with self.store._db() as db:
            r=self.store._get(db,'inv'); path=r['ordered_image_paths'][0]; digest=r['generation_digests'][path]
            r['quality_cycle']['attempts'][-1].update(status='review_pending',digest=digest)
            r.update(state='qc_pending',candidate_path=path,uploads={'candidate':{'sha256':digest,'path':path,'artifact_id':'123'}}); self.store._put(db,r)
        a=self.store.next()
        report={'viewed_digests':a['input_digests'],'quality_binding':a['quality_binding'],'review_binding':a['review_binding'],
                'checks':[{'name':n,'status':'passed','evidence':'synthetic fixture'} for n in a['required_checks']],
                'skill_report':{'schema_version':1,'candidate':'candidate_1','scores':{'garment_construction':90,'color_material':88,'garment_details':88,'target_preservation':90,'text_layout':95},'critical_defects':[],'primary_defect':None,'evidence':['synthetic fixture'],'confidence':0.85,'decision':'accept','exact_text':True,'added_text':[],'missing_text':[],'instances_exact':True,'panel_count_exact':True,'panel_layout_exact':True}}
        self.store.qc('inv',report); self.assertIsNotNone(self.store.result('inv'))

    def test_inventory_invalid_submission_budget_is_durable(self):
        self.begin(); a=self.store.next(); bad=self.envelope(a); bad['reading_round']=9
        for _ in range(3):
            with self.assertRaises(ValueError): self.store.inventory('inv',bad)
        self.assertEqual(self.store.next()['reason'],'inventory_submission_exhausted')
        self.assertFalse(self.store.dispatch('inv',caps(),{})['should_invoke'])

    def test_binding_schema_and_restart(self):
        self.begin(); a=self.store.next(); bad=self.envelope(a); bad['target_digest']='0'*64
        with self.assertRaises(ValueError): self.store.inventory('inv',bad)
        self.assertEqual(self.store.next()['reading_round'],1)
        self.store.inventory('inv',self.envelope(a)); reopened=type(self.store)(self.store.path,'identity')
        self.assertEqual(reopened.next()['reading_round'],2)
