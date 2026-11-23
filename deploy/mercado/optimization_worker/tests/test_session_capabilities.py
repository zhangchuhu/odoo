import unittest


class SessionCapabilitiesTest(unittest.TestCase):
    def test_session_providers_only_include_operational_text_host(self):
        from optimization_worker.__main__ import session_providers
        provider = object()
        self.assertEqual(session_providers(provider), {
            'research.keywords': provider, 'title.generate': provider,
        })

    def test_explicit_interactive_host_retains_all_session_capabilities(self):
        from optimization_worker.__main__ import session_providers
        from optimization_worker.handlers import TASK_TYPES
        provider = object()
        self.assertEqual(set(session_providers(provider, interactive=True)), set(TASK_TYPES) - {'product.facts'})


if __name__ == '__main__':
    unittest.main()
