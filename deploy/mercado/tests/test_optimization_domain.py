"""Pure domain behavior; namespace loading avoids addon/Odoo model initialization."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import sys
import types
import unittest
from pathlib import Path

addon = Path(__file__).resolve().parents[1] / 'addons' / 'meli_optimization'
pkg = types.ModuleType('optimization_unit')
pkg.__path__ = [str(addon)]
sys.modules.setdefault('optimization_unit', pkg)
try:
    domain = importlib.import_module('optimization_unit.domain')
except ModuleNotFoundError as exc:
    if not exc.name.startswith('optimization_unit.domain'):
        raise
    domain = None


def evidence():
    return [{'id': 'source:any-id', 'source': 'https://example.test/item',
             'observed_at': '2026-11-15T00:00:00Z'}]

class VariantPictureGroups(unittest.TestCase):
    def test_skipped_is_a_terminal_task_state(self):
        domain.validate_transition('running', 'skipped')
        for state in ('queued', 'running', 'succeeded'):
            with self.assertRaises(domain.DomainError):
                domain.validate_transition('skipped', state)
    def test_publishing_other_sizes_preserves_scope_after_one_size_changes(self):
        m=importlib.import_module('optimization_unit.domain.variant_groups')
        scope={'key':'k','variant_ids':['11','12'],'site_variant_ids':['101','102'],'picture_ids':['old'],'attribute_signature':'color'}
        current={'groups':[{'key':'k','variant_ids':['11','12'],'attribute_signature':'color'}],'site_variation_ids':['101','102']}
        target={'variants':[{'id':'11','picture_ids':['new']},{'id':'12','picture_ids':['old']}]}
        self.assertEqual(m.publishable_variants(scope,current,target),['12'])
        with self.assertRaises(ValueError):m.publishable_variants(scope,dict(current,site_variation_ids=['101']),target)
        changed=dict(current,groups=[dict(current['groups'][0],attribute_signature='other')])
        with self.assertRaises(ValueError):m.publishable_variants(scope,changed,target)
    def test_screen_checkpoint_retains_variant_picture_identity_without_secrets(self):
        m=importlib.import_module('optimization_unit.domain.screening_resume')
        item={'id':'CBT1','site_id':'CBT','seller_id':'8','access_token':'secret',
              'pictures':[{'id':'p','secure_url':'https://http2.mlstatic.com/a.jpg'}],
              'variations':[{'id':11,'picture_ids':['p'],'attribute_combinations':[{'id':'COLOR','value_name':'Gray'}]}],
              'marketplace_items':[{'item_id':'MLM1','user_id':'9','parent_id':'CBT1','parent_user_id':'8','site_id':'MLM','logistic_type':'remote','token':'secret'}]}
        result=m.minimized('/marketplace/items/CBT1',item)
        self.assertEqual(result['variations'][0]['picture_ids'],['p'])
        self.assertEqual(result['marketplace_items'][0]['parent_id'],'CBT1')
        self.assertNotIn('secret',repr(result))
    def test_site_variations_map_to_parent_groups_only_by_unambiguous_pictures(self):
        m=importlib.import_module('optimization_unit.domain.variant_groups')
        groups=[{'picture_ids':['p1'],'variant_ids':['11','12']},{'picture_ids':['p2'],'variant_ids':['13']}]
        site=[{'id':101,'picture_ids':['p1'],'available_quantity':2},{'id':102,'picture_ids':['p1'],'available_quantity':3},{'id':103,'picture_ids':['p2'],'available_quantity':4}]
        result=m.bind_site_variations(groups,site)
        self.assertEqual(result[0]['site_variant_ids'],['101','102'])
        self.assertEqual(result[0]['stock'],5)
        with self.assertRaises(ValueError):m.bind_site_variations([groups[0],groups[0]],site)
        with self.assertRaises(ValueError):m.bind_site_variations(groups,site[:-1])
    def test_groups_color_sizes_without_mixing_images(self):
        m=importlib.import_module('optimization_unit.domain.variant_groups')
        def v(i,color,code,size,imgs):
            return {'id':i,'attribute_combinations':[{'id':'COLOR','value_id':code,'value_name':color},{'id':'SIZE','value_id':size,'value_name':size}], 'picture_ids':imgs,'available_quantity':2}
        data={'pictures':[{'id':'p1','secure_url':'https://http2.mlstatic.com/a.jpg'},{'id':'p2','secure_url':'https://http2.mlstatic.com/b.jpg'}],
              'variations':[v(11,'Violeta','v','S',['p1']),v(12,'Violeta','v','M',['p1']),v(13,'Gray','g','S',['p2'])]}
        groups=m.traditional_groups(data)
        self.assertEqual(len(groups),2)
        self.assertEqual(groups[0]['variant_ids'],['11','12'])
        self.assertEqual(groups[0]['picture_ids'],['p1'])
        self.assertEqual(groups[1]['picture_ids'],['p2'])
        self.assertEqual(groups[0]['stock'],4)
        changed=copy.deepcopy(data);changed['variations'][1]['picture_ids']=['p2']
        self.assertFalse(m.traditional_groups(changed)[0]['selectable'])
        changed=copy.deepcopy(data);changed['variations'][0]['picture_ids']=['missing']
        self.assertFalse(m.traditional_groups(changed)[0]['selectable'])

class ImagePublishScope(unittest.TestCase):
    def test_resolves_user_product_only_from_matching_verified_parent(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        item = {'id':'MLM123', 'site_id':'MLM', 'seller_id':22, 'cbt_item_id':'CBT456',
                'user_product_id':'MLMU999', 'tags':['user_product_listing']}
        parent = {'id':'CBT456', 'seller_id':11, 'site_id':'CBT', 'marketplace_items':[
            {'item_id':'MLM123','site_id':'MLM','user_id':22,'logistic_type':'remote',
             'parent_id':'CBT456','parent_user_id':11,'user_product_id':'MLMU999','siteless_user_product_id':'U999'}]}
        result = module.resolve_target(item, parent, 'MLM123', 'MLM', '11', '22', 'remote')
        self.assertEqual((result['model'], result['target_id']), ('user_product','U999'))
        for key, value in [('user_id',23),('item_id','MLM999'),('siteless_user_product_id','bad'),('user_product_id','MLMU123')]:
            altered=copy.deepcopy(parent); altered['marketplace_items'][0][key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.resolve_target(item, altered, 'MLM123', 'MLM', '11', '22', 'remote')

    def test_rejects_unverified_or_multi_variant_traditional_scope(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        item={'id':'MLM123','site_id':'MLM','seller_id':22,'cbt_item_id':'CBT456','tags':[]}
        parent={'id':'CBT456','site_id':'CBT','seller_id':11,'variations':[{'id':1},{'id':2}], 'marketplace_items':[
            {'item_id':'MLM123','site_id':'MLM','user_id':22,'logistic_type':'remote','parent_id':'CBT456','parent_user_id':11}]}
        with self.assertRaises(ValueError): module.resolve_target(item,parent,'MLM123','MLM','11','22','remote')
        with self.assertRaises(ValueError): module.resolve_target(dict(item,tags=None),parent,'MLM123','MLM','11','22','remote')
        parent['variations']=[]
        self.assertEqual(module.resolve_target(item,parent,'MLM123','MLM','11','22','remote')['model'],'traditional')

    def test_update_payload_is_exact_ordered_full_picture_list(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        pictures = ['111-CBT123_012026', '222-CBT123_012026']
        self.assertEqual(module.picture_update_payload(pictures), {'pictures':[{'id':pictures[0]},{'id':pictures[1]}]})
        for invalid in ([], [pictures[0], pictures[0]], ['../bad'], [None], pictures * 26):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): module.picture_update_payload(invalid)

    def test_preflight_reads_identity_and_all_affected_sites(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        item = {'id':'MLM123','site_id':'MLM','seller_id':22,'cbt_item_id':'CBT456','user_product_id':'MLMU999','tags':['user_product_listing']}
        parent = {'id':'CBT456','site_id':'CBT','seller_id':11,'category_id':'CBT321','marketplace_items':[
            {'item_id':'MLM123','site_id':'MLM','user_id':22,'logistic_type':'remote','parent_id':'CBT456','parent_user_id':11,
             'user_product_id':'MLMU999','siteless_user_product_id':'U999'},
            {'item_id':'MLB987','site_id':'MLB','user_id':33,'logistic_type':'remote','parent_id':'CBT456','parent_user_id':11,
             'user_product_id':'MLBU999','siteless_user_product_id':'U999'}]}
        responses={'/users/me':{'id':11},'/marketplace/users/11':{'user_id':11,'marketplaces':[{'user_id':22,'site_id':'MLM','logistic_type':'remote'}]},
                   '/marketplace/items/MLM123':item,'/marketplace/items/CBT456':parent,
                   '/categories/CBT321':{'id':'CBT321','settings':{'max_pictures_per_item':12}},
                   '/user-products/U999':{'id':'U999','user_id':11,'pictures':[{'id':'111-CBT123_012026','secure_url':'https://http2.mlstatic.com/D_NQ_NP_123.jpg'}]}}
        result=module.collect_preflight(lambda path: responses[path], 'MLM123','MLM','11','22','remote')
        self.assertEqual(result['picture_ids'],['111-CBT123_012026'])
        self.assertEqual(result['picture_limit'],12)
        self.assertEqual(result['pictures'][0]['url'], 'https://http2.mlstatic.com/D_NQ_NP_123.jpg')
        self.assertEqual([s['site_id'] for s in result['affected_sites']],['MLM','MLB'])
        responses['/users/me']={'id':12}
        with self.assertRaises(ValueError): module.collect_preflight(lambda path: responses[path], 'MLM123','MLM','11','22','remote')

    def test_publication_steps_never_repeat_an_uncertain_write(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        state={'phase':'ready','uploaded':[],'total':2}
        first=module.reserve_step(state,'a'*32)
        self.assertEqual((first['phase'],first['operation'],first['index']),('in_flight','upload',0))
        dispatched=module.mark_dispatched(first,'a'*32)
        self.assertTrue(dispatched['dispatched'])
        with self.assertRaises(ValueError): module.mark_dispatched(dispatched,'a'*32)
        with self.assertRaises(ValueError): module.reserve_step(first,'b'*32)
        unknown=module.finish_step(dispatched,'a'*32,'unknown')
        self.assertEqual(unknown['phase'],'reconciliation_required')
        self.assertEqual(unknown['code'],'platform_outcome_unknown')
        with self.assertRaises(ValueError): module.reserve_step(unknown,'c'*32)
        with self.assertRaises(ValueError): module.finish_step(first,'a'*32,'success',picture_id='111-CBT123_012026')
        done=module.finish_step(dispatched,'a'*32,'success',picture_id='111-CBT123_012026')
        second=module.reserve_step(done,'b'*32)
        self.assertEqual(second['index'],1)
        done=module.finish_step(module.mark_dispatched(second,'b'*32),'b'*32,'success',picture_id='222-CBT123_012026')
        update=module.reserve_step(done,'c'*32)
        self.assertEqual(update['operation'],'update')
        update=module.mark_dispatched(update,'c'*32)
        verified=module.finish_step(update,'c'*32,'success',observed_ids=done['uploaded'])
        self.assertEqual(verified['phase'],'published')
        uncertain=module.finish_step(update,'c'*32,'success',observed_ids=[])
        self.assertEqual(uncertain['phase'],'reconciliation_required')
        self.assertEqual(uncertain['code'],'platform_images_mismatch')

    def test_upload_envelope_has_a_single_image_and_safe_boundary(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        body, content_type = module.image_upload_form(b'png-bytes', 'image/png', 'a' * 32)
        self.assertEqual(content_type, 'multipart/form-data; boundary=' + 'a'*32)
        self.assertIn(b'name="file"; filename="image.png"', body)
        self.assertIn(b'Content-Type: image/png\r\n\r\npng-bytes\r\n', body)
        for raw,mime,boundary in [(b'', 'image/png', 'a'*32), (b'x','image/webp','a'*32), (b'x','image/png','../bad')]:
            with self.assertRaises(ValueError): module.image_upload_form(raw,mime,boundary)

    def test_readback_requires_correct_product_and_seller(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        product={'id':'U999','user_id':11,'pictures':[{'id':'111-CBT123_012026'}]}
        self.assertEqual(module.user_product_picture_ids(product,'U999','11'),['111-CBT123_012026'])
        for bad in (dict(product,id='U998'),dict(product,user_id=12),dict(product,pictures=[])):
            with self.assertRaises(ValueError): module.user_product_picture_ids(bad,'U999','11')

    def test_traditional_publication_links_each_uploaded_image_before_replacement(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        state={'phase':'ready','uploaded':['111-CBT123_012026','222-CBT123_012026'],'linked':[], 'total':2, 'model':'traditional'}
        first=module.mark_dispatched(module.reserve_step(state,'a'*32),'a'*32)
        self.assertEqual((first['operation'],first['index']),('link',0))
        with self.assertRaises(ValueError): module.finish_step(first,'a'*32,'success',picture_id='222-CBT123_012026')
        state=module.finish_step(first,'a'*32,'success',picture_id='111-CBT123_012026')
        second=module.mark_dispatched(module.reserve_step(state,'b'*32),'b'*32)
        self.assertEqual((second['operation'],second['index']),('link',1))
        state=module.finish_step(second,'b'*32,'success',picture_id='222-CBT123_012026')
        self.assertEqual(module.reserve_step(state,'c'*32)['operation'],'update')

    def test_legacy_scope_check_permits_only_our_known_linked_images(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        original={'model':'traditional','target_id':'CBT456','parent_id':'CBT456','item_id':'MLM123',
                  'affected_sites':[{'item_id':'MLM123','site_id':'MLM'}], 'picture_ids':['111-CBT123_012026'], 'pictures':[]}
        current=dict(original,picture_ids=['111-CBT123_012026','222-CBT123_012026'])
        self.assertTrue(module.legacy_scope_matches(original,current,['222-CBT123_012026']))
        self.assertFalse(module.legacy_scope_matches(original,current,[]))
        self.assertFalse(module.legacy_scope_matches(original,dict(current,target_id='CBT999'),['222-CBT123_012026']))

    def test_legacy_variation_update_preserves_every_other_sku(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        variants=[{'id':'12','picture_ids':['111-CBT123_012026']},{'id':'13','picture_ids':['222-CBT123_012026']}]
        target={'picture_ids':['111-CBT123_012026','222-CBT123_012026'],'variants':variants,'selected_variant_id':'12'}
        result=module.legacy_update_payload(target,['333-CBT123_012026'])
        self.assertEqual(result['pictures'],[{'id':'333-CBT123_012026'},{'id':'111-CBT123_012026'},{'id':'222-CBT123_012026'}])
        self.assertEqual(result['variations'],[{'id':'12','picture_ids':['333-CBT123_012026']},{'id':'13','picture_ids':['222-CBT123_012026']}])
        current=dict(target,picture_ids=[p['id'] for p in result['pictures']],variants=result['variations'])
        self.assertTrue(module.legacy_update_matches(target,current,['333-CBT123_012026']))
        current['variants'][1]['picture_ids']=['333-CBT123_012026']
        self.assertFalse(module.legacy_update_matches(target,current,['333-CBT123_012026']))
        with self.assertRaises(ValueError): module.legacy_update_payload(dict(target,selected_variant_id='99'),['333-CBT123_012026'])
        with self.assertRaises(ValueError): module.legacy_update_payload(dict(target,variants=[{'id':[],'picture_ids':[]}]),['333-CBT123_012026'])

    def test_traditional_variations_require_explicit_selection(self):
        module = importlib.import_module('optimization_unit.domain.image_publish')
        item={'id':'MLM123','site_id':'MLM','seller_id':22,'cbt_item_id':'CBT456','tags':[]}
        parent={'id':'CBT456','site_id':'CBT','seller_id':11,'pictures':[{'id':'111-CBT123_012026'},{'id':'222-CBT123_012026'}],
                'marketplace_items':[{'item_id':'MLM123','site_id':'MLM','user_id':22,'logistic_type':'remote','parent_id':'CBT456','parent_user_id':11}],
                'variations':[{'id':12,'picture_ids':['111-CBT123_012026'],'seller_custom_field':'BLACK-S','attribute_combinations':[{'name':'Talla','value_name':'S'}]},
                              {'id':13,'picture_ids':['222-CBT123_012026'],'seller_custom_field':'BLACK-M'}]}
        pending=module.resolve_target(item,parent,'MLM123','MLM','11','22','remote')
        self.assertIsNone(pending['selected_variant_id'])
        self.assertEqual([v['id'] for v in pending['variants']],['12','13'])
        self.assertEqual(pending['variants'][0]['attributes'], ['Talla: S'])
        selected=module.resolve_target(item,parent,'MLM123','MLM','11','22','remote','12')
        self.assertEqual(selected['selected_variant_id'],'12')
        with self.assertRaises(ValueError): module.resolve_target(item,parent,'MLM123','MLM','11','22','remote','99')

class InternalImageQCContract(unittest.TestCase):
    def test_manual_accept_preserves_report_and_identifies_decision(self):
        digest = 'a' * 64
        qc = {'status':'manual_accepted','candidate_sha256':digest,'report':{'decision':'reject'},'decision_event_id':42}
        domain.validate_internal_image_qc({'image':{'sha256':digest},'visual_qc':qc})
        for invalid in (dict(qc, decision_event_id=0), dict(qc, decision_event_id=True), dict(qc, candidate_sha256='b'*64)):
            with self.assertRaises(domain.DomainError):
                domain.validate_internal_image_qc({'image':{'sha256':digest},'visual_qc':invalid})
    def test_completed_legacy_report_can_be_checked_without_accepting_new_legacy_results(self):
        digest = 'a' * 64
        names = ('garment_construction', 'color_material', 'garment_details', 'identity_pose',
                 'accessories', 'scene_lighting_framing', 'text_layout')
        report = {'quality_binding': {'candidate_sha256': digest}, 'skill_report': {'decision': 'accept'},
                  'checks': [{'name': name, 'status': 'passed'} for name in names]}
        result = {'image': {'sha256': digest}, 'visual_qc': report}
        domain.validate_internal_image_qc(result, allow_legacy=True)
        with self.assertRaises(domain.DomainError):
            domain.validate_internal_image_qc(result)
        report['checks'][0]['status'] = 'failed'
        with self.assertRaises(domain.DomainError):
            domain.validate_internal_image_qc(result, allow_legacy=True)

    def test_passed_report_binds_the_actual_image(self):
        digest = 'a' * 64
        result = {'image': {'sha256': digest}, 'visual_qc': {'status': 'passed', 'candidate_sha256': digest, 'report': {'decision': 'accept'}}}
        domain.validate_internal_image_qc(result)
        for qc in ({'status': 'failed', 'candidate_sha256': digest, 'report': {}},
                   {'status': 'passed', 'candidate_sha256': digest, 'report': {}},
                   {'status': 'passed', 'candidate_sha256': 'b' * 64, 'report': {}},
                   {'candidate_sha256': digest, 'report': {}}, None):
            with self.subTest(qc=qc), self.assertRaises(domain.DomainError):
                domain.validate_internal_image_qc({'image': {'sha256': digest}, 'visual_qc': qc})


def titles():
    return {'schema_version': '1', 'evidence': evidence(),
            'candidates': [{'text': 'Vestido elegante modelo %s' % n,
                            'evidence_ids': ['source:any-id']} for n in range(10)]}


def research():
    return {'schema_version': '1', 'status': 'partial', 'reason': 'pool_exhausted',
            'evidence': evidence(), 'candidates': [{
                'platform': 'MLM', 'item_id': 'MLM123',
                'url': 'https://example.test/item', 'raw_url': 'https://example.test/item?x=1',
                'query': 'vestido', 'position': 1, 'is_ad': None,
                'sold_display': '+500 vendidos', 'sold_lower_bound': 500,
                'sold_upper_bound': None, 'review_count': None, 'rating': None,
                'image_url': 'https://example.test/image.jpg', 'visual_category': 'dress',
                'observed_at': '2026-11-15T00:00:00Z',
                'selection_reason': 'same garment', 'evidence_ids': ['source:any-id']} ]}


def gallery():
    return {'schema_version': '1', 'reference_item_id': 'MLM123',
            'selected_variant': {'color': 'black'}, 'observed_at': '2026-11-15T00:00:00Z',
            'gallery_manifest_hash': 'a' * 64, 'completeness_status': 'complete',
            'evidence': evidence(), 'images': [{
                'image_id': 'img-1', 'order': 0, 'role': 'front', 'artifact_id': 'art:1',
                'source': 'https://example.test/image.jpg', 'evidence_ids': ['source:any-id'],
                'mime_type': 'image/jpeg', 'width': 1200, 'height': 1600,
                'sha256': 'b' * 64}]}


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(domain, 'pure optimization domain implementation is missing')

    def reject(self, code, function, *args):
        with self.assertRaises(domain.DomainError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(str(caught.exception))

    def test_hash_canonicalizes_nested_key_order_and_unicode(self):
        expected = hashlib.sha256('{"a":["ñ",1],"z":null}'.encode()).hexdigest()
        self.assertEqual(domain.canonical_hash({'z': None, 'a': ['ñ', 1]}), expected)
        self.assertNotEqual(domain.canonical_hash([1, 2]), domain.canonical_hash([2, 1]))

    def test_hash_rejects_unpaired_surrogate_keys_and_values(self):
        for value in ({'bad': '\ud800'}, {'\udfff': 'bad'}):
            with self.subTest(value=repr(value)):
                self.reject('invalid_json', domain.canonical_hash, value)

    def test_non_json_values_are_rejected_not_coerced(self):
        for value in (float('nan'), float('inf'), {1: 'bad'}, {'x': (1, 2)}, {'x': object()}):
            with self.subTest(value=value):
                self.reject('invalid_json', domain.canonical_hash, value)

    def test_input_types_have_required_fields_and_preserve_extensions(self):
        cases = {
            'product.facts': {'product_snapshot_id': 'snap'},
            'research.keywords': {'facts_artifact_id': 'facts', 'site_id': 'MLM'},
            'research.bestseller': {'facts_artifact_id': 'facts', 'keywords_artifact_id': 'kw', 'site_id': 'MLM'},
            'gallery.collect': {'reference_item_id': 'item', 'selected_variant': {'size': 'M'}},
            'title.generate': {'facts_artifact_id': 'facts', 'keywords_artifact_id': 'kw', 'language': 'es-MX'},
            'image.generate': {'source_artifact_ids': ['source'], 'target_artifact_id': 'target', 'generation_plan_id': 'plan'},
            'image.qc': {'image_artifact_id': 'image', 'generation_plan_id': 'plan'},
        }
        for task_type, fields in cases.items():
            value = dict(fields, schema_version='1', extension={'unknown': None})
            with self.subTest(task_type=task_type):
                self.assertIs(domain.validate_input(task_type, value), value)
                for field in fields:
                    bad = value.copy(); del bad[field]
                    self.reject('invalid_input', domain.validate_input, task_type, bad)
        self.reject('unsupported_task', domain.validate_input, 'not.a.task', {})
        self.reject('unsupported_task', domain.validate_result, 'not.a.task', {})
        self.reject('invalid_input', domain.validate_input, 'product.facts', {'schema_version': '2'})

    def test_transition_policy_blocks_terminal_reopen_and_skipped_execution(self):
        for before, after in [('draft', 'queued'), ('queued', 'running'), ('running', 'succeeded'),
                              ('running', 'waiting'), ('waiting', 'queued'), ('running', 'retry_wait'),
                              ('retry_wait', 'queued'), ('draft', 'cancelled')]:
            domain.validate_transition(before, after)
        for before, after in [('succeeded', 'queued'), ('failed', 'running'), ('queued', 'succeeded'),
                              ('draft', 'draft'), ('unknown', 'queued')]:
            self.reject('invalid_transition', domain.validate_transition, before, after)

    def test_lease_rejects_expiry_stale_token_epoch_and_non_utc_time(self):
        now = datetime(2026, 11, 15, tzinfo=timezone.utc)
        active = {'state': 'running', 'lease_token': 'lease', 'lease_epoch': 2}
        domain.validate_lease(active, 'lease', 2, now + timedelta(seconds=1), now)
        for token, epoch, expiry in [('old', 2, now + timedelta(seconds=1)),
                                      ('lease', 1, now + timedelta(seconds=1)), ('lease', 2, now)]:
            self.reject('invalid_lease', domain.validate_lease, active, token, epoch, expiry, now)
        self.reject('invalid_lease', domain.validate_lease, dict(active, state='queued'), 'lease', 2, now + timedelta(seconds=1), now)
        self.reject('invalid_lease', domain.validate_lease, active, 'lease', True, now + timedelta(seconds=1), now)
        self.reject('invalid_lease', domain.validate_lease, active, 'lease', 2, now.replace(tzinfo=None), now)

    def test_titles_require_ten_distinct_grounded_candidates_without_length_cap(self):
        value = titles(); value['candidates'][0]['text'] = 'Vestido ' + 'elegante ' * 100
        self.assertIs(domain.validate_title_candidates(value), value)
        self.assertIs(domain.validate_result('title.generate', value), value)
        for mutation in ('count', 'duplicate', 'evidence', 'blank'):
            bad = titles()
            if mutation == 'count': bad['candidates'].pop()
            elif mutation == 'duplicate': bad['candidates'][1]['text'] = '  VESTIDO   ELEGANTE MODELO 0 '
            elif mutation == 'blank': bad['candidates'][1]['text'] = '  '
            else: bad['candidates'][1]['evidence_ids'] = ['invented']
            self.reject('invalid_result', domain.validate_title_candidates, bad)

    def test_research_keeps_unknown_metrics_and_raw_sales_and_explicit_status(self):
        value = research(); original = copy.deepcopy(value)
        self.assertIs(domain.validate_research_candidates(value), value)
        self.assertEqual(value, original)
        self.assertIs(domain.validate_result('research.bestseller', value), value)
        empty = dict(value, status='empty', candidates=[])
        domain.validate_research_candidates(empty)
        for updates in ({'status': 'complete', 'candidates': []}, {'status': 'empty'},
                        {'reason': ''}, {'status': 'success'}):
            self.reject('invalid_result', domain.validate_research_candidates, dict(value, **updates))

    def test_research_rejects_fabricated_evidence_and_mistyped_metrics(self):
        for field, replacement in [('evidence_ids', ['invented']), ('sold_lower_bound', True),
                                   ('rating', '4.5'), ('review_count', -1), ('is_ad', 'unknown')]:
            bad = research(); bad['candidates'][0][field] = replacement
            self.reject('invalid_result', domain.validate_research_candidates, bad)
        bad = research(); bad['evidence'][0]['observed_at'] = 'yesterday'
        self.reject('invalid_result', domain.validate_research_candidates, bad)

    def test_gallery_requires_identity_order_image_metadata_and_evidence(self):
        value = gallery()
        self.assertIs(domain.validate_gallery(value), value)
        self.assertIs(domain.validate_result('gallery.collect', value), value)
        for field, replacement in [('width', 0), ('height', True), ('mime_type', 'text/plain'),
                                   ('sha256', 'bad'), ('evidence_ids', []), ('artifact_id', '')]:
            bad = gallery(); bad['images'][0][field] = replacement
            self.reject('invalid_result', domain.validate_gallery, bad)
        bad = gallery(); bad['images'].append(dict(bad['images'][0], image_id='different'))
        self.reject('invalid_result', domain.validate_gallery, bad)

    def test_other_results_validate_typed_evidence_and_quality_outcomes(self):
        cases = {
            'product.facts': {'facts': [{'name': 'category', 'value': 'dress', 'evidence_ids': ['source:any-id']}], 'uncertainties': []},
            'research.keywords': {'status': 'complete', 'keywords': [{'text': 'vestido', 'evidence_ids': ['source:any-id']}]},
            'image.generate': {'image': gallery()['images'][0], 'request_id': 'req-1', 'visual_qc': {'status': 'passed', 'candidate_sha256': gallery()['images'][0]['sha256'], 'report': {'decision': 'accept'}}},
            'image.qc': {'image_artifact_id': 'img', 'status': 'passed', 'checks': [
                {'name': 'garment', 'status': 'passed', 'evidence_ids': ['source:any-id']}]},
        }
        for task_type, fields in cases.items():
            value = dict(fields, schema_version='1', evidence=evidence())
            with self.subTest(task_type=task_type):
                self.assertIs(domain.validate_result(task_type, value), value)
                self.reject('invalid_result', domain.validate_result, task_type, {'schema_version': '1'})
        bad = dict(cases['image.qc'], schema_version='1', evidence=evidence())
        bad['checks'][0]['status'] = 'unknown'
        self.reject('invalid_result', domain.validate_result, 'image.qc', bad)
