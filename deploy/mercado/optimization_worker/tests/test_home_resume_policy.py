import unittest

from optimization_worker.home_resume_policy import resume_page_matches


class HomeResumePolicyTests(unittest.TestCase):
    def test_shein_resume_requires_explicit_platform_and_original_product(self):
        observed={'url':'https://us.shein.com/x-p-123.html','product_id':'123','blocked':False}
        self.assertTrue(resume_page_matches('detail',{'product_id':'123','platform':'shein-us'},observed))
        self.assertFalse(resume_page_matches('detail',{'product_id':'123'},observed))
        self.assertFalse(resume_page_matches('detail',{'product_id':'124','platform':'shein-us'},observed))
        search={'url':'https://us.shein.com/pdsearch/dress/','query':'dress','blocked':False}
        self.assertTrue(resume_page_matches('search',{'query':'dress','platform':'shein-us'},search))
        self.assertFalse(resume_page_matches('search',{'query':'other','platform':'shein-us'},search))
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
