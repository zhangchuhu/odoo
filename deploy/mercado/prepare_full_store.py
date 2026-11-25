import csv,json
from pathlib import Path
import requests
s=env['meli.independent.store'].browse(1)
assert s.state=='connected'
r=requests.get('https://api.mercadolibre.com/marketplace/users/'+s.seller_id,headers={'Authorization':'Bearer '+s.access_token},timeout=25)
r.raise_for_status()
markets=[x for x in r.json()['marketplaces'] if x['site_id']=='MLM' and x['logistic_type']=='fulfillment']
assert len(markets)==1
market=markets[0]
assert str(market['user_id'])=='3618161625'
s.write({'logistic_type':'fulfillment','marketplace_seller_id':str(market['user_id']),'business_model':market.get('business_model'),'pricing_model':market.get('pricing_model'),'marketplace_user_product':market.get('user_product',False)})
env.cr.commit()
c=env['res.company'].browse(1)
m=env['meli.util'].get_new_instance(c)
assert not m.need_login()
products=env['product.product'].with_context(active_test=False).search([('meli_id','!=',False)],order='id')
rows=[]
items=[]
for p in products:
    d=m.get('/items/'+p.meli_id,{'access_token':m.access_token,'include_attributes':'all'}).json()
    assert d.get('id')==p.meli_id,(p.meli_id,d.get('error'))
    attrs={a['id']:a.get('value_name') for a in d.get('attributes',[])}
    variations=d.get('variations',[])
    items.append({k:d.get(k) for k in ['id','title','category_id','currency_id','price','pictures','attributes','variations','status','domain_id']})
    for v in variations or [{}]:
        va={a['id']:a.get('value_name') for a in v.get('attributes',[])}
        row={'odoo_product_id':p.id,'source_item':p.meli_id,'title':d.get('title'),'variation_id':v.get('id',''),'sku':va.get('SELLER_SKU') or v.get('seller_custom_field') or attrs.get('SELLER_SKU') or d.get('seller_custom_field') or '', 'options':'; '.join(a.get('name','')+': '+str(a.get('value_name','')) for a in v.get('attribute_combinations',[])), 'source_price':v.get('price',d.get('price')),'source_currency':d.get('currency_id'),'target_price':'','target_currency':'USD','gtin':va.get('GTIN') or attrs.get('GTIN') or '', 'target_site':'MLM','target_logistics':'fulfillment','target_seller':s.marketplace_seller_id}
        rows.append(row)
root=Path('deploy/mercado/reports')
(root/'full-source-products.json').write_text(json.dumps(items,ensure_ascii=False,indent=2))
with (root/'full-listing-review.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
env.cr.commit()
print(json.dumps({'store':s.name,'site_seller':s.marketplace_seller_id,'logistics':s.logistic_type,'business_model':s.business_model,'pricing_model':s.pricing_model,'source_listings':len(items),'sku_rows':len(rows),'missing_sku':sum(not r['sku'] for r in rows),'missing_gtin':sum(not r['gtin'] for r in rows),'currencies':sorted(set(r['source_currency'] for r in rows))},ensure_ascii=False))
