"""The registry must fail closed before any operation can be exposed."""
import unittest
from copy import deepcopy
from odoo.addons.meli_platform_adapter.registry import OperationRegistry, RegistryError

BASE = {
 'operation_id':'items.read', 'domain':'items', 'title':'Read item',
 'description':'Read a shop item', 'docs_url':'https://global-selling.mercadolibre.com/devsite/global-listing',
 'verified_at':'2026-11-07', 'input_schema':{'type':'object','properties':{'item_id':{'type':'string','pattern':'^CBT[0-9]+$'}},'required':['item_id'],'additionalProperties':False},
 'permission':'read', 'scope':'shop', 'capability_requirements':[], 'ownership_policy':'item',
 'handler_id':'get_item', 'upstream':{'method':'GET','path':'/items/{item_id}'},
 'request_kind':'json','response_kind':'json','exposure':'mcp','retry_policy':'never',
}

def build(*rows):
 return OperationRegistry(rows,handlers={'get_item'},policies={'item'})

class RegistryTests(unittest.TestCase):
 def test_duplicate_and_missing_metadata_rejected(self):
  with self.assertRaises(RegistryError):build(BASE,BASE)
  for field in BASE:
   item=deepcopy(BASE);del item[field]
   with self.subTest(field=field),self.assertRaises(RegistryError):build(item)
 def test_invalid_handler_scope_and_upstream_are_rejected(self):
  for key,value in [('handler_id','evil'),('ownership_policy','missing'),('permission','admin'),('scope','maybe'),('upstream',{'method':'GET','path':'https://evil.test'}),('upstream',{'method':'GET','path':'//evil.test'})]:
   item=deepcopy(BASE);item[key]=value
   with self.subTest(key=key,value=value),self.assertRaises(RegistryError):build(item)
 def test_bad_schema_and_unknown_fields_fail_validation(self):
  item=deepcopy(BASE);item['input_schema']={'type':'invalid'}
  with self.assertRaises(RegistryError):build(item)
  r=build(BASE);r.validate('items.read',{'item_id':'CBT123'})
  for args in ({},{'item_id':'CBT1','identity':3},{'item_id':'../other'}):
   with self.assertRaises(RegistryError):r.validate('items.read',args)
 def test_catalog_redacts_internal_details_and_cannot_mutate_registry(self):
  r=build(BASE);before=r.version;result=r.catalog()[0]
  self.assertNotIn('handler_id',result);self.assertNotIn('upstream',result);self.assertNotIn('ownership_policy',result)
  result['input_schema']['properties']['item_id']['pattern']='.*'
  self.assertEqual(r.version,before)
  with self.assertRaises(RegistryError):r.validate('items.read',{'item_id':'../other'})
 def test_version_is_read_only(self):
  r=build(BASE)
  with self.assertRaises(AttributeError): r.version='forged'
 def test_non_url_or_invalid_port_rejected(self):
  for url in ('https://evil.test/docs','https://global-selling.mercadolibre.com:abc/docs'):
   row=deepcopy(BASE);row['docs_url']=url
   with self.assertRaises(RegistryError):build(row)
 def test_version_stable_for_order_and_changes_with_contract(self):
  second=deepcopy(BASE);second['operation_id']='items.read_other'
  self.assertEqual(build(BASE,second).version,build(second,BASE).version)
  altered=deepcopy(BASE);altered['input_schema']['properties']['item_id']['maxLength']=50
  self.assertNotEqual(build(BASE).version,build(altered).version)

if __name__=='__main__': unittest.main()
