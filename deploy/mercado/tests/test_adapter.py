import sys
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'addons'))
from meli_platform_adapter import api, oauth
from meli_platform_adapter.errors import PlatformError

class AdapterTests(unittest.TestCase):
 def test_fixed_origin_and_no_redirect_or_environment_credentials(self):
  with patch.object(api.requests,'Session') as factory:
   session=factory.return_value.__enter__.return_value
   api.request('POST','/global/items',token='secret',json={'title':'lamp'})
   self.assertFalse(session.trust_env)
   args,kw=session.request.call_args
   self.assertEqual(args,('POST','https://api.mercadolibre.com/global/items'))
   self.assertEqual(kw['headers']['Authorization'],'Bearer secret')
   self.assertFalse(kw['allow_redirects'])
   self.assertEqual(session.request.call_count,1)
 def test_reject_arbitrary_origin_and_identity_override(self):
  for path in ('https://evil.test','//evil.test','/items/../oauth/token','/items?access_token=x'):
   with self.assertRaises(PlatformError): api.request('GET',path,token='secret')
 def test_network_error_is_redacted_and_not_retried(self):
  with patch.object(api.requests,'Session') as factory:
   session=factory.return_value.__enter__.return_value
   session.request.side_effect=api.requests.Timeout('secret')
   with self.assertRaises(api.requests.RequestException) as ctx: api.request('POST','/global/items',token='secret')
   self.assertNotIn('secret',str(ctx.exception));self.assertEqual(session.request.call_count,1)
 def test_oauth_exchange_uses_adapter(self):
  with patch.object(oauth.api,'request') as req:
   oauth.exchange({'grant_type':'authorization_code','code':'test'})
   self.assertEqual(req.call_args.args,('POST','/oauth/token'))
   self.assertEqual(req.call_args.kwargs['data']['code'],'test')

if __name__=='__main__': unittest.main()
