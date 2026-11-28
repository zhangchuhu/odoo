import unittest

from optimization_worker.home_suggestions import rank_suggestions, initial_queries, verify_sealed_queries


class HomeSuggestionTests(unittest.TestCase):
    def test_relevance_dedup_and_observed_order(self):
        observations=[{'text':'Conjunto mujer formal','position':1},
                      {'text':'Conjunto mujer falda blusa','position':2},
                      {'text':'conjunto MUJER falda blusa','position':3},
                      {'text':'conjunto mujer pantalon','position':4}]
        rows=rank_suggestions('womens_skirt_set',observations,[])
        self.assertEqual([r['text'] for r in rows],['Conjunto mujer falda blusa','Conjunto mujer formal'])
        self.assertTrue(all(r['source']=='platform_suggestion' for r in rows))

    def test_real_mexico_suggestions_reject_english_pants_for_skirt_set(self):
        observations=[{'text':'Conjunto mujer elegante','position':1},{'text':'Conjunto mujer casual','position':2},
                      {'text':'Conjunto mujer pants','position':3},{'text':'Conjunto mujer verano','position':4}]
        rows=rank_suggestions('womens_skirt_set',observations,[])
        self.assertEqual([row['text'] for row in rows],['Conjunto mujer elegante','Conjunto mujer casual','Conjunto mujer verano'])

    def test_fill_from_image_without_claiming_observation(self):
        result=initial_queries('conjunto mujer falda',[],['conjunto mujer falda calado','conjunto mujer falda elegante'])
        self.assertEqual([x['source'] for x in result],['category_anchor','image_fallback','image_fallback'])
        self.assertEqual(result[0]['text'],'conjunto mujer falda')

    def test_platform_source_requires_an_actual_ranked_observation(self):
        with self.assertRaises(ValueError):
            initial_queries('conjunto mujer falda',['conjunto mujer falda blusa'],[])
        with self.assertRaises(ValueError):
            initial_queries('conjunto mujer falda',[{'text':'conjunto mujer falda blusa','source':'image_fallback','position':1}],[])

    def test_insufficient_observed_or_image_terms_does_not_invent_queries(self):
        with self.assertRaises(ValueError):initial_queries('conjunto mujer falda',[],[])

    def test_plan_cannot_claim_unobserved_platform_suggestions(self):
        suggestions=rank_suggestions('womens_skirt_set',[{'text':'conjunto mujer falda blusa','position':2}],[])
        selected=initial_queries('conjunto mujer falda',suggestions,['conjunto mujer falda elegante'])
        self.assertTrue(verify_sealed_queries(selected,suggestions,['conjunto mujer falda elegante']))
        bad=[dict(x) for x in selected];bad[1]['text']='conjunto mujer falda formal'
        self.assertFalse(verify_sealed_queries(bad,suggestions,['conjunto mujer falda elegante']))

    def test_input_validation_and_unknown_category(self):
        with self.assertRaises(ValueError):rank_suggestions('womens_skirt_set',[{'text':'x','position':True}],[])
        self.assertEqual(rank_suggestions('unknown',[{'text':'conjunto mujer formal','position':1}],[]),[])

    def test_platform_suggestions_cover_supported_non_set_categories(self):
        for category,text in [('top','top mujer elegante'),('blouse','blusa mujer formal'),('jumpsuit','mono mujer elegante'),('skirt','falda mujer larga'),('coat','abrigo mujer formal')]:
            with self.subTest(category=category):
                self.assertEqual([row['text'] for row in rank_suggestions(category,[{'text':text,'position':1}],[])],[text])

    def test_dress_suggestions_cannot_mix_skirt_or_pants(self):
        rows=rank_suggestions('dress',[{'text':'vestido mujer largo','position':1},
                                      {'text':'vestido con pantalon','position':2}],[])
        self.assertEqual([x['text'] for x in rows],['vestido mujer largo'])

    def test_no_color_or_size_and_missing_evidence(self):
        items=[{'text':'conjunto mujer falda rosa','position':1},{'text':'conjunto mujer falda talla m','position':2}]
        self.assertEqual(rank_suggestions('womens_skirt_set',items,[]),[])

    def test_url_and_control_characters_are_not_search_terms(self):
        with self.assertRaises(ValueError):
            rank_suggestions('womens_skirt_set',[{'text':'conjunto mujer falda https://example.test','position':1}],[])
        with self.assertRaises(ValueError):
            initial_queries('conjunto mujer falda',[],['conjunto mujer falda\nmalicious'])
