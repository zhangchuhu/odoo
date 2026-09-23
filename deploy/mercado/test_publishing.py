from unittest.mock import patch, Mock
import requests
from odoo import api
from odoo.exceptions import UserError, AccessError
from odoo.addons.meli_accounts.publishing import Publication
from odoo.addons.meli_accounts import publishing

env=api.Environment(env.cr,2,dict(env.context,allowed_company_ids=[1]))
Job=env['meli.store.publication']; Store=env['meli.independent.store']
store=Store.browse(1)
product=env['product.product'].search([('meli_id','!=',False)],limit=1)
before=env['product.product'].search([('meli_id','!=',False)],order='id').read(['meli_id','product_tmpl_id'])
target={'seller':store.seller_id,'site_seller':store.marketplace_seller_id,'site':'MLM','logistics':'fulfillment','user_products':True}
seq=0

def make_job():
    global seq
    seq+=1
    j=super(Publication,Job).create({'store_id':store.id,'product_id':product.id,
        'source_item':product.meli_id,'source_variation':'test-'+str(seq),'sku':'TEST-'+str(seq),
        'family_name':'Lace Dress','english_confirmed':True,'price':35.8,'gtin':'740145140291',
        'source_data':{'category_id':'CBT123','pictures':[{'secure_url':'https://http2.mlstatic.com/test.jpg'}]}})
    for code in ['PACKAGE_HEIGHT','PACKAGE_LENGTH','PACKAGE_WIDTH','PACKAGE_WEIGHT']:
        env['meli.store.publication.attribute']._add(j,code,code,'10 cm' if code!='PACKAGE_WEIGHT' else '200 g')
    return j

def reject(fn):
    try: fn()
    except (UserError,AccessError): return
    raise AssertionError('Expected rejection')

with patch.object(type(env.cr),'commit',lambda self:None), \
     patch.object(type(store),'_publishing_target',lambda self:target), \
     patch.object(type(store),'_get',lambda self,path,params=None:[]), \
     patch.object(type(store),'_token',lambda self:'fake-token'), \
     patch.object(type(Job),'_upload_images',lambda self,token:['picture-test']):
    j=make_job()
    reject(lambda:j.write({'state':'done'}))
    reject(lambda:Job.with_user(env.ref('base.public_user')).search([]))
    reject(lambda:j.action_publish())
    j.action_check(); assert j.state=='ready'
    b=j._build(target)
    assert 'family_name' in b and 'title' not in b and 'variations' not in b
    assert 'available_quantity' not in b and 'shipping' not in b and 'global_net_proceeds' not in b
    assert b['sites_to_sell']==[{'site_id':'MLM','logistic_type':'fulfillment','listing_type_id':'gold_special','price':35.8}]
    j.attribute_ids[0].write({'value':'12 cm'});assert j.state=='draft'
    j.action_check();j.action_publish();assert j.state=='queued'
    reject(lambda:j.action_publish())
    reject(lambda:j.write({'gtin':'12345678'}))
    reject(lambda:j.attribute_ids[0].write({'value':'20 cm'}))
    response=Mock(ok=True,status_code=201)
    response.json.return_value={'id':'CBT999999000001','status':'paused'}
    with patch.object(publishing.requests,'post',return_value=response) as post:
        j._publish_one();assert j.state=='done';assert post.call_count==1
        j._publish_one();assert post.call_count==1
        assert post.call_args.kwargs['json']['pictures']==[{'id':'picture-test'}]
    assert j.target_item=='CBT999999000001'
    assert product.meli_id==j.source_item
    t=make_job();t.action_check();t.action_publish()
    with patch.object(publishing.requests,'post',side_effect=requests.Timeout):
        t._publish_one();assert t.state=='uncertain'
    reject(lambda:t.action_publish());t.action_check();assert t.state=='uncertain'
    f=make_job();f.action_check();f.action_publish()
    response=Mock(ok=False,status_code=400)
    response.json.return_value={'message':'Invalid category fake-token','error':'validation_error'}
    with patch.object(publishing.requests,'post',return_value=response):
        f._publish_one();assert f.state=='failed';assert 'fake-token' not in f.result
    f.action_check();assert f.state=='ready'
    m=make_job();m.write({'gtin':False});m.action_check();assert m.state=='blocked' and 'GTIN' in m.check_result
    e=make_job();e.write({'english_confirmed':False});e.action_check();assert e.state=='blocked'
    c=make_job();c.action_check();c.action_publish();c.action_cancel_queue();assert c.state=='draft'
    assert env['product.product'].search([('meli_id','!=',False)],order='id').read(['meli_id','product_tmpl_id'])==before
    print('PASS: permissions, protected fields, payload isolation, Full stock exclusion, preflight, edits invalidate checks, queue cancellation, duplicate submission guard, success mapping, timeout hold, 400 retry, credential redaction, original 28 links unchanged.')
env.cr.rollback()
