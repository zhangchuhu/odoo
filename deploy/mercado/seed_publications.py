from odoo import api
from odoo.addons.meli_accounts.publishing import Publication

env=api.Environment(env.cr,2,dict(env.context,allowed_company_ids=[1]))
products=env['product.product'].search([('meli_id','!=',False)],order='id')
before=products.read(['meli_id','product_tmpl_id'])
wizard=env['meli.store.prepare'].create({'store_id':1,'product_ids':[(6,0,products.ids)]})
action=wizard.action_prepare()
jobs=env['meli.store.publication'].search(action['domain'])
translations={
'CBT5158001616':'Purple V-Neck Vest and Pants Set for Women',
'CBT5157695166':'Black Top and Pants Two-Piece Set for Women',
'CBT5157436128':'Beige Lace Dress with Puff Sleeves and Bow',
'CBT4265479687':'Pink Button Top and Wide-Leg Pants Set',
'CBT4265475701':'Elegant Jacket and Wide-Leg Pants Suit',
'CBT4265254913':'Pink Crop Top and Palazzo Pants Set for Women',
'CBT5180509360':'Blue Lace Puff-Sleeve Top and Midi Skirt Set',
'CBT5180509352':'Three-Piece Lace Blouse Skirt and Top Set',
'CBT5180407264':'Lace Dress with Puff Sleeves and Belt',
'CBT5178586414':'Green Sleeveless Top and Dress Pants Set with Bow',
'CBT5178584598':'Gray Sleeveless Top and Dress Pants Set with Bow',
'CBT5178207022':'Sleeveless Top and Dress Pants Set for Women',
'CBT5177930002':'Black and White Casual Top and Pants Set for Women',
'CBT4285622857':'Short-Sleeve Lace Dress with Buttons',
'CBT4285611131':'Pink Knot Crop Blouse and Pants Set for Women',
'CBT4285611117':'White Knot Crop Blouse and Pants Set for Women',
'CBT4285599065':'White Boho Sleeveless Top and Long Skirt Set',
'CBT4285598941':'V-Neck Sleeveless Top and Dress Pants Set',
'CBT4284941793':'V-Neck Sleeveless Top and Tailored Pants Set',
'CBT5185033510':'Pink Vest and Wide-Leg Pants Set for Women',
'CBT5194384400':'Blue Lace Shirt-Collar Dress with Belt',
'CBT4300586805':'Cream Lace Shirt-Collar Dress with Belt',
'CBT5214844234':'Blue Lace Blouse and Skirt Two-Piece Set',
'CBT5214840542':'White Embroidered Shirt-Collar Dress with Bow',
'CBT5244578926':'Fuchsia Long Cutout Shirt-Collar Dress with Slit',
'CBT5256315030':'Gray Color-Block Top and Wide-Leg Pants Set',
'CBT4362165861':'Two-Piece Embroidered Lace Party Dress',
'CBT5287191246':'Beige Tailored Clothing Set with Wide-Leg Pants and Belt',
}
for job in jobs.filtered(lambda j:j.state in ('draft','blocked')):
    if job.source_item in translations:
        job.write({'family_name':translations[job.source_item],'english_confirmed':True})
env.cr.commit()
print('PREPARED',len(jobs),'SKU for',len(products),'products',flush=True)
jobs.action_check()
env.cr.commit()
assert products.read(['meli_id','product_tmpl_id'])==before
for state in ['draft','blocked','ready','queued','done']:
    print(state,len(jobs.filtered(lambda j:j.state==state)),flush=True)
for reason in sorted(set(jobs.filtered(lambda j:j.state=='blocked').mapped('check_result'))):
    print('BLOCKER',reason,flush=True)
print('ORIGINAL_LINKS_UNCHANGED',len(products),flush=True)
