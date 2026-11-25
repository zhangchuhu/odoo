import unittest
from optimization_worker.research_capability_probe import check_research_capabilities, probe_odoo_read, chrome_event_verified, ReadinessEpoch, ProbeResult


class ResearchCapabilityProbeTest(unittest.TestCase):
    def test_only_fresh_bound_results_from_every_probe_are_ready(self):
        now = lambda: 100.0
        probes = {key: (lambda nonce: {'nonce': nonce, 'observed_at': 100.0, 'ok': True}) for key in ('chrome', 'vision', 'callback')}
        self.assertTrue(check_research_capabilities(probes, now=now).ready)

    def test_missing_stale_future_wrong_nonce_and_exception_fail_closed(self):
        good = lambda nonce: {'nonce': nonce, 'observed_at': 100.0, 'ok': True}
        base = dict(chrome=good, vision=good, callback=good)
        bad = [None, lambda n: {'nonce': n, 'observed_at': 69, 'ok': True}, lambda n: {'nonce': n, 'observed_at': 101, 'ok': True}, lambda n: {'nonce': 'other', 'observed_at': 100, 'ok': True}, lambda n: 1, lambda n: (_ for _ in ()).throw(RuntimeError())]
        for probe in bad:
            with self.subTest(probe=probe):
                self.assertFalse(check_research_capabilities(dict(base, chrome=probe), now=lambda:100).ready)

    def test_claimed_boolean_or_metadata_is_not_a_probe(self):
        self.assertFalse(check_research_capabilities({'chrome': True, 'vision': True, 'callback': True}, now=lambda:100).ready)

    def test_odoo_read_probe_calls_authenticated_capabilities_without_claiming_write(self):
        class Client:
            def request(self, method, path):
                self.call = method, path
                return {'task_types': ['research.bestseller'], 'live_execution': False}
        client = Client()
        result = probe_odoo_read(client)
        self.assertEqual(client.call, ('GET', '/api/meli/optimization/capabilities'))
        self.assertTrue(result.read_authorized)
        self.assertFalse(result.write_verified)

    def test_odoo_read_failure_or_unexpected_envelope_fails_closed(self):
        class Client:
            def __init__(self, result): self.result = result
            def request(self, *args):
                if isinstance(self.result, Exception): raise self.result
                return self.result
        for result in (RuntimeError('sensitive value'), {}, {'task_types': 'research.bestseller'}):
            with self.subTest(result=type(result)):
                value = probe_odoo_read(Client(result))
                self.assertFalse(value.read_authorized)
                self.assertFalse(value.write_verified)

    def test_chrome_probe_requires_completed_cua_call_and_nonerror_result(self):
        item = {'type': 'mcp_tool_call', 'server': 'cua_repl', 'tool': 'js', 'status': 'completed', 'error': None, 'arguments': {'code': 'let app = await cua.getApp("Google Chrome");'}, 'result': {'content': [{'type': 'text', 'text': 'Window: Google Chrome'}]}}
        self.assertTrue(chrome_event_verified(item))
        for changed in ({'status':'failed'}, {'error':{'message':'x'}}, {'arguments':{'code':'text("Google Chrome")'}}, {'result':{'isError':True}}, {'server':'other'}):
            self.assertFalse(chrome_event_verified(dict(item, **changed)))

    def test_readiness_expires_and_never_survives_new_process_epoch(self):
        clock = [100.0]
        current = ReadinessEpoch(now=lambda: clock[0], ttl=10)
        self.assertFalse(current.ready('host-a'))
        current.accept('host-a', ProbeResult(True, ()))
        self.assertTrue(current.ready('host-a'))
        self.assertFalse(current.ready('host-b'))
        restarted = ReadinessEpoch(now=lambda: clock[0], ttl=10)
        self.assertFalse(restarted.ready('host-a'))
        clock[0] = 111
        self.assertFalse(current.ready('host-a'))

    def test_negative_probe_and_backwards_clock_revoke_readiness(self):
        clock = [100.0]
        gate = ReadinessEpoch(now=lambda: clock[0], ttl=10)
        gate.accept('host-a', ProbeResult(True, ()))
        clock[0] = 99
        self.assertFalse(gate.ready('host-a'))
        clock[0] = 100
        gate.accept('host-a', ProbeResult(False, ('callback',)))
        self.assertFalse(gate.ready('host-a'))

class SlowCapabilitiesTests(unittest.TestCase):
    def test_ninety_second_vision_is_not_confused_with_freshness(self):
        clock=[0.0]
        def vision(nonce):
            clock[0]+=90
            return {'nonce':nonce,'observed_at':clock[0],'ok':True}
        def quick(nonce): return {'nonce':nonce,'observed_at':clock[0],'ok':True}
        result=check_research_capabilities({'vision':vision,'chrome':quick,'callback':quick},now=lambda:clock[0],max_duration=120,max_age=30)
        self.assertTrue(result.ready)
    def test_slow_chrome_does_not_age_out_vision(self):
        clock = [0.0]
        calls = []
        def adapter(kind, duration):
            def probe(nonce):
                calls.append(kind)
                clock[0] += duration
                return {'nonce':nonce, 'observed_at':clock[0], 'ok':True}
            return probe
        probes = {k:adapter(k, t) for k,t in [('vision',8),('chrome',44),('callback',1)]}
        result = check_research_capabilities(probes,now=lambda:clock[0])
        self.assertTrue(result.ready)
        self.assertEqual(calls,['chrome','vision','callback'])
        self.assertEqual(result.expires_at,74)

    def test_expired_proofs_are_observed_again_with_a_new_challenge(self):
        clock = [0.0]
        calls = []
        def quick(nonce):
            calls.append(nonce)
            return {'nonce':nonce, 'observed_at':clock[0], 'ok':True}
        def callback(nonce):
            clock[0] += 31
            return quick(nonce)
        result = check_research_capabilities(dict(vision=quick,chrome=quick,callback=callback),now=lambda:clock[0])
        self.assertTrue(result.ready)
        self.assertEqual(len(set(calls)),5)
        self.assertEqual(result.expires_at,61)

    def test_second_round_cannot_authorize_with_old_proof(self):
        clock = [0.0]
        def slow(nonce):
            clock[0] += 35
            return {'nonce':nonce, 'observed_at':clock[0], 'ok':True}
        result = check_research_capabilities(dict(vision=slow,chrome=slow,callback=slow),now=lambda:clock[0])
        self.assertFalse(result.ready)
        self.assertEqual(result.expired,('chrome','callback'))
        self.assertIsNone(result.expires_at)


class SingleFlightReadinessTests(unittest.TestCase):
    def test_concurrent_callers_share_fresh_probe_and_expire(self):
        import threading
        from optimization_worker.research_capability_probe import SingleFlightReadiness
        clock=[10.0];entered=threading.Event();release=threading.Event();calls=[]
        def probe(): calls.append(1);entered.set();release.wait(2);return True
        cache=SingleFlightReadiness(probe,now=lambda:clock[0],ttl=5)
        results=[]
        a=threading.Thread(target=lambda:results.append(cache.ready()));a.start();self.assertTrue(entered.wait(1))
        b=threading.Thread(target=lambda:results.append(cache.ready()));b.start();release.set();a.join(2);b.join(2)
        self.assertEqual(results,[True,True]);self.assertEqual(len(calls),1)
        clock[0]=16;self.assertTrue(cache.ready());self.assertEqual(len(calls),2)
    def test_failure_is_not_cached_as_ready_and_restart_has_no_memory(self):
        from optimization_worker.research_capability_probe import SingleFlightReadiness
        state=[False];calls=[]
        def probe():calls.append(1);return state[0]
        c=SingleFlightReadiness(probe,now=lambda:100,ttl=5)
        self.assertFalse(c.ready());state[0]=True;self.assertTrue(c.ready())
        other=SingleFlightReadiness(probe,now=lambda:100,ttl=5)
        self.assertTrue(other.ready());self.assertEqual(len(calls),3)

    def test_cached_result_never_outlives_evidence_expiry(self):
        from optimization_worker.research_capability_probe import SingleFlightReadiness, ProbeResult
        mono=[0];wall=[100];calls=[]
        def probe(): calls.append(1);return ProbeResult(True,(),105)
        c=SingleFlightReadiness(probe,now=lambda:mono[0],wall=lambda:wall[0],ttl=10)
        self.assertTrue(c.ready());mono[0]=6;wall[0]=106
        self.assertFalse(c.ready());self.assertEqual(len(calls),2)
