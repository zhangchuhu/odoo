import importlib
import unittest
import test_optimization_domain  # Installs the isolated addon namespace.

batch = importlib.import_module('optimization_unit.domain.image_batch_plan')


class ImageBatchPlanTest(unittest.TestCase):
    def test_ready_publication_state_uses_the_completed_operation(self):
        pmod=importlib.import_module('optimization_unit.domain.image_publish')
        self.assertEqual(pmod.ready_publication_state('update'), 'updating')
        self.assertEqual(pmod.ready_publication_state('link'), 'linking')
        self.assertEqual(pmod.ready_publication_state('upload'), 'uploading')

    def test_diagnostic_redacts_credentials_and_urls(self):
        pmod=importlib.import_module('optimization_unit.domain.image_publish')
        item=pmod.safe_publish_diagnostic('link','response',403,{'error':'forbidden','message':'Bearer abc123 https://example.com/a?access_token=xyz'})
        self.assertEqual(item['http_status'],403)
        self.assertEqual(item['code'],'forbidden')
        self.assertNotIn('abc123',item['message'])
        self.assertNotIn('access_token',item['message'])
        item=pmod.safe_publish_diagnostic('link','response',400,{'message':'Invalid','cause':[{'code':'item.pictures.limit','message':'too many pictures'}]})
        self.assertIn('item.pictures.limit',item['message'])
    def test_defines_picture_groups_sizes_using_category_metadata(self):
        attributes = [{'id':'COLOR','tags':{'defines_picture':True}}, {'id':'SIZE','tags':{}}]
        variants = [
            {'id':'1','attribute_combinations':[{'id':'COLOR','value_id':'red','value_name':'Red'},{'id':'SIZE','value_name':'S'}],'picture_ids':['old']},
            {'id':'2','attribute_combinations':[{'id':'COLOR','value_id':'red','value_name':'Red'},{'id':'SIZE','value_name':'M'}],'picture_ids':['old']},
            {'id':'3','attribute_combinations':[{'id':'COLOR','value_id':'blue','value_name':'Blue'},{'id':'SIZE','value_name':'S'}],'picture_ids':['other']},
        ]
        groups = batch.picture_groups(attributes, variants)
        self.assertEqual([x['variant_ids'] for x in groups], [['1','2'],['3']])
        self.assertEqual(groups[0]['picture_ids'], ['old'])
        self.assertEqual(groups[0]['labels'], ['COLOR: Red'])
        contracts=importlib.import_module('optimization_unit.domain.contracts')
        self.assertTrue(contracts.canonical_hash(groups))

    def test_inconsistent_group_pictures_are_not_publishable(self):
        attrs = [{'id':'COLOR','tags':{'defines_picture':True}}]
        variants = [{'id':str(i),'attribute_combinations':[{'id':'COLOR','value_id':'red'}],'picture_ids':[pic]} for i,pic in [(1,'a'),(2,'b')]]
        with self.assertRaisesRegex(ValueError, '图片'):
            batch.picture_groups(attrs, variants)

    def test_preflight_groups_raw_variations_by_picture_attribute(self):
        publishing=importlib.import_module('optimization_unit.domain.image_publish')
        mapping={'item_id':'MLM123','site_id':'MLM','user_id':22,'logistic_type':'remote','parent_id':'CBT456','parent_user_id':11}
        item={'id':'MLM123','site_id':'MLM','seller_id':22,'cbt_item_id':'CBT456','tags':[]}
        variant=lambda i, size: {'id':i,'picture_ids':['a'],'attribute_combinations':[{'id':'COLOR','value_id':'red','value_name':'Red'}, {'id':'SIZE','value_name':size}]}
        parent={'id':'CBT456','site_id':'CBT','seller_id':11,'category_id':'CBT321','marketplace_items':[mapping],
                'pictures':[{'id':'a'}], 'variations':[variant(1,'S'),variant(2,'M')]}
        responses={'/users/me':{'id':11}, '/marketplace/users/11':{'user_id':11,'marketplaces':[{'user_id':22,'site_id':'MLM','logistic_type':'remote'}]},
                   '/marketplace/items/MLM123':item, '/marketplace/items/CBT456':parent,
                   '/items/CBT456':parent,
                   '/categories/CBT321':{'id':'CBT321','settings':{'max_pictures_per_item':12,'max_pictures_per_item_var':10}},
                   '/categories/CBT321/attributes':[{'id':'COLOR','tags':{'defines_picture':True}}, {'id':'SIZE','tags':{}}]}
        result=publishing.collect_preflight(lambda path: responses[path], 'MLM123','MLM','11','22','remote','1')
        self.assertEqual(result['selected_group_variant_ids'],['1','2'])
        import copy
        conflicting=copy.deepcopy(parent);conflicting['variations'].pop()
        responses['/items/CBT456']=conflicting
        with self.assertRaises(ValueError):
            publishing.collect_preflight(lambda path: responses[path], 'MLM123','MLM','11','22','remote','1')

    def test_scope_comparison_accepts_platform_reordering_only(self):
        m=importlib.import_module('optimization_unit.domain.image_publish')
        base={'model':'traditional','target_id':'CBT1','parent_id':'CBT1','item_id':'MLM1','affected_sites':[],
              'category_id':'CBT2','picture_limit':12,'variant_picture_limit':10,'selected_group_variant_ids':['1','2'],
              'variants':[{'id':'1','sku':'S','attributes':['Color: Black','Size: S']},{'id':'2','sku':'M','attributes':['Color: Black','Size: M']}],
              'picture_groups':[{'key':[['COLOR',['id','black']]],'variant_ids':['1','2']}]}
        import copy
        current=copy.deepcopy(base);current['selected_group_variant_ids'].reverse();current['variants'].reverse();current['picture_groups'][0]['variant_ids'].reverse()
        self.assertTrue(m.group_scope_matches(base,current))
        current['variants'][0]['sku']='wrong'
        self.assertFalse(m.group_scope_matches(base,current))

    def test_missing_picture_defining_attribute_fails_closed(self):
        with self.assertRaises(ValueError):
            batch.picture_groups([{'id':'SIZE','tags':{}}], [{'id':'1','attribute_combinations':[], 'picture_ids':['a']}])

    def test_category_picture_attributes_unused_by_every_variant_are_ignored(self):
        attrs = [{'id':'COLOR','tags':{'defines_picture':True}},{'id':'FABRIC_DESIGN','tags':{'defines_picture':True}}]
        variants = [{'id':'1','attribute_combinations':[{'id':'COLOR','value_id':'black'}], 'picture_ids':['a']},
                    {'id':'2','attribute_combinations':[{'id':'COLOR','value_id':'black'}], 'picture_ids':['a']}]
        self.assertEqual(batch.picture_groups(attrs,variants)[0]['variant_ids'], ['1','2'])
        variants[0]['attribute_combinations'].append({'id':'FABRIC_DESIGN','value_name':'Plain'})
        with self.assertRaises(ValueError): batch.picture_groups(attrs,variants)

    def test_batch_plan_keeps_group_consistent_and_other_variants_unchanged(self):
        old=['a','b','c','d','e','f']; new=[f'n{i}' for i in range(9)]
        variants=[{'id':'1','picture_ids':old}, {'id':'2','picture_ids':old}]
        plan=batch.plan_batches(old, variants, ['1','2'], new, 12, 12)
        self.assertEqual(plan[-1]['root'],new)
        self.assertEqual(plan[-1]['variants'],{'1':new,'2':new})
        self.assertTrue(any(s['kind']=='update' and s['variants']['1'] != new for s in plan))
        for step in plan:
            self.assertLessEqual(len(step['root']),12)
            self.assertEqual(step['variants']['1'],step['variants']['2'])
            self.assertTrue(step['variants']['1'])
            self.assertTrue(set(step['variants']['1']).issubset(step['root']))

    def test_global_updates_replace_group_incrementally_within_limits(self):
        old=['a','b','c','d','e','f']; new=[f'n{i}' for i in range(9)]
        variants=[{'id':'1','picture_ids':old}, {'id':'2','picture_ids':old}]
        steps=batch.plan_global_batches(old, variants, ['1','2'], new, 12, 10)
        self.assertTrue(all(s['kind']=='update' for s in steps))
        self.assertTrue(all(len(s['root'])<=12 and 0<len(s['variants']['1'])<=10 for s in steps))
        self.assertTrue(all(s['variants']['1']==s['variants']['2'] for s in steps))
        self.assertEqual(steps[-1]['root'],new)
        self.assertEqual(steps[-1]['variants']['1'],new)

    def test_global_updates_preserve_shared_and_other_variant_images(self):
        variants=[{'id':'1','picture_ids':['a','shared']}, {'id':'2','picture_ids':['shared','other']}]
        steps=batch.plan_global_batches(['a','shared','other'],variants,['1'],['new'],3,2)
        self.assertEqual(steps[-1]['root'],['new','shared','other'])
        self.assertEqual(steps[-1]['variants']['2'],['shared','other'])
        self.assertEqual(steps[-1]['variants']['1'],['new'])

    def test_global_updates_refuse_final_limit_excess(self):
        with self.assertRaisesRegex(ValueError,'最终'):
            batch.plan_global_batches(['a'],[{'id':'1','picture_ids':['a']}],['1'],['n1','n2','n3'],2,2)

    def test_cannot_remove_last_target_image_to_free_space(self):
        variants=[{'id':'1','picture_ids':['a']},{'id':'2','picture_ids':['b']}]
        with self.assertRaisesRegex(ValueError,'空间'):
            batch.plan_batches(['a','b'],variants,['1'],['n1'],2,2)

    def test_linked_new_image_can_replace_last_old_image_to_continue_batching(self):
        plan=batch.plan_batches(['a','b'], [{'id':'1','picture_ids':['a','b']}], ['1'], ['n1','n2'], 2, 2)
        self.assertEqual(plan[-1]['root'], ['n1','n2'])
        self.assertTrue(all(s['variants']['1'] for s in plan))

    def test_final_limit_or_missing_target_is_rejected(self):
        variants=[{'id':'1','picture_ids':['a']}]
        with self.assertRaisesRegex(ValueError,'最终'):
            batch.plan_batches(['a'],variants,['1'],['n1','n2','n3'],2,3)
        with self.assertRaises(ValueError):
            batch.plan_batches(['a'],variants,['2'],['n1'],2,2)

    def test_other_variant_images_and_gallery_order_are_preserved(self):
        variants=[{'id':'1','picture_ids':['a','b']},{'id':'2','picture_ids':['c']}]
        plan=batch.plan_batches(['a','b','c'],variants,['1'],['n1','n2'],4,3)
        self.assertEqual(plan[-1]['root'],['n1','n2','c'])
        self.assertTrue(all(step['variants']['2']==['c'] for step in plan))

    def test_step_payload_includes_all_variations_and_preserves_shared_images(self):
        variants=[{'id':'1','picture_ids':['a','shared']}, {'id':'2','picture_ids':['shared','c']}]
        plan=batch.plan_batches(['a','shared','c'],variants,['1'],['n'],4,3)
        update=batch.batch_update_payload(plan[-1])
        self.assertEqual(update, {'pictures':[{'id':'n'},{'id':'shared'},{'id':'c'}],
            'variations':[{'id':'1','picture_ids':['n']},{'id':'2','picture_ids':['shared','c']}]})

    def test_tampering_with_reserved_plan_is_rejected(self):
        plan=batch.plan_batches(['a','b'],[{'id':'1','picture_ids':['a','b']}],['1'],['n'],3,3)
        p=batch.start_plan(plan, {'root':['a','b'],'variants':{'1':['a','b']}})
        p['plan'][0]['root'].append('outside')
        with self.assertRaises(ValueError): batch.reserve_batch_step(p, 'a'*32)

    def test_success_response_still_requires_platform_readback(self):
        plan=batch.plan_batches(['a','b'],[{'id':'1','picture_ids':['a','b']}],['1'],['n'],3,3)
        p=batch.start_plan(plan, {'root':['a','b'],'variants':{'1':['a','b']}})
        p=batch.dispatch_batch_step(batch.reserve_batch_step(p, 'a'*32), 'a'*32)
        p=batch.finish_batch_step(p, 'a'*32, 'success')
        self.assertEqual(p['phase'], 'reconciliation_required')
        p=batch.reconcile_batch_step(p, {'root':plan[0]['root'], 'variants':plan[0]['variants']})
        self.assertEqual((p['phase'],p['index']), ('ready',1))

    def test_group_publication_uploads_then_executes_verified_batch(self):
        pmod=importlib.import_module('optimization_unit.domain.image_publish')
        p={'phase':'ready','model':'traditional_group','uploaded':[], 'total':1,
           'target':{'picture_ids':['a','b'],'variants':[{'id':'1','picture_ids':['a','b']},{'id':'2','picture_ids':['a','b']}],
                     'selected_group_variant_ids':['1','2'],'picture_limit':3,'variant_picture_limit':3}}
        p=pmod.mark_dispatched(pmod.reserve_step(p,'a'*32),'a'*32)
        p=pmod.finish_step(p,'a'*32,'success',picture_id='n')
        p=pmod.mark_dispatched(pmod.reserve_step(p,'b'*32),'b'*32)
        self.assertEqual(p['operation'],'update')
        expected=p['batch']['plan'][0]
        p=pmod.finish_step(p,'b'*32,'success',observed_ids={'root':expected['root'],'variants':expected['variants']})
        self.assertEqual(p['phase'],'published')

    def test_uncertain_step_cannot_repeat_and_only_matching_readback_advances(self):
        plan=batch.plan_batches(['a','b'],[{'id':'1','picture_ids':['a','b']}],['1'],['n'],3,3)
        initial={'root':['a','b'],'variants':{'1':['a','b']}}
        progress=batch.start_plan(plan, initial)
        progress=batch.reserve_batch_step(progress, 'a'*32)
        progress=batch.dispatch_batch_step(progress, 'a'*32)
        progress=batch.finish_batch_step(progress, 'a'*32, 'unknown')
        with self.assertRaises(ValueError): batch.reserve_batch_step(progress,'b'*32)
        unchanged=batch.reconcile_batch_step(progress, initial)
        self.assertEqual(unchanged['phase'],'reconciliation_required')
        resolved=batch.reconcile_batch_step(progress, {'root':plan[0]['root'],'variants':plan[0]['variants']})
        self.assertEqual((resolved['phase'],resolved['index']),('ready',1))

    def test_stale_or_unexpected_readback_never_advances(self):
        plan=batch.plan_batches(['a','b'],[{'id':'1','picture_ids':['a','b']}],['1'],['n'],3,3)
        p=batch.start_plan(plan, {'root':['a','b'],'variants':{'1':['a','b']}})
        p=batch.dispatch_batch_step(batch.reserve_batch_step(p,'a'*32),'a'*32)
        with self.assertRaises(ValueError): batch.finish_batch_step(p,'b'*32,'success')
        p=batch.finish_batch_step(p,'a'*32,'unknown')
        self.assertEqual(batch.reconcile_batch_step(p,{'root':['outside'],'variants':{'1':['outside']}})['phase'],'reconciliation_required')

    def test_explicit_retry_requires_unchanged_platform_state(self):
        plan=batch.plan_batches(['a','b'],[{'id':'1','picture_ids':['a','b']}],['1'],['n'],3,3)
        initial={'root':['a','b'],'variants':{'1':['a','b']}}
        p=batch.start_plan(plan, initial)
        p=batch.dispatch_batch_step(batch.reserve_batch_step(p,'a'*32),'a'*32)
        p=batch.finish_batch_step(p,'a'*32,'unknown')
        with self.assertRaises(ValueError): batch.retry_batch_step(p,{'root':['outside'],'variants':{'1':['outside']}})
        ready=batch.retry_batch_step(p,initial)
        self.assertEqual((ready['phase'],ready['index']),('ready',0))
