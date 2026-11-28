import unittest

from optimization_worker.home_resume_policy import resume_page_matches


class HomeResumePolicyTests(unittest.TestCase):
    def test_search_requires_current_mexico_site_and_exact_query(self):
        observed={'url':'https://listado.mercadolibre.com.mx/conjunto-mujer-falda','query':'conjunto mujer falda','blocked':False}
        self.assertTrue(resume_page_matches('search',{'query':'conjunto mujer falda'},observed))
        self.assertFalse(resume_page_matches('search',{'query':'conjunto mujer falda'},dict(observed,query='conjunto mujer pantalon')))
        self.assertFalse(resume_page_matches('search',{'query':'conjunto mujer falda'},dict(observed,blocked=True)))
        self.assertFalse(resume_page_matches('search',{'query':'conjunto mujer falda'},dict(observed,url='https://example.org/query')))

    def test_detail_requires_same_product_identity(self):
        observed={'url':'https://articulo.mercadolibre.com.mx/MLM-12345-test','product_id':'MLM12345','blocked':False}
        self.assertTrue(resume_page_matches('detail',{'product_id':'MLM12345'},observed))
        self.assertFalse(resume_page_matches('detail',{'product_id':'MLM99999'},observed))
