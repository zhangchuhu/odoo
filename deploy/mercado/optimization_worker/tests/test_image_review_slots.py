import unittest
from unittest.mock import Mock, patch
from optimization_worker.runtime import Worker

class ReviewSlotsTests(unittest.TestCase):
    def test_parked_review_is_heartbeat_processed_and_new_task_claimed(self):
        from optimization_worker.image_service import review_parked
        parked = {'claim': {'id': 1, 'task_type': 'image.generate'}, 'request_id': 'one', 'phase': 'host_pending'}
        client = Mock(); client.identity='test'; client.claim.return_value = {'id':2, 'task_type':'image.generate'}
        store=Mock(); store.inspect.return_value = {'state':'waiting', 'reason':'candidate_qc_unsettled'}
        journal=Mock(); journal.active.return_value=[parked]
        worker=Worker(client, '/tmp/unused', capabilities=('image.generate',), park=lambda r: review_parked(store,r))
        with patch('optimization_worker.runtime.Journal', return_value=journal), patch.object(worker, '_run', side_effect=['host_pending','running']) as run:
            self.assertEqual(worker.once(), 'running')
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[1],parked)
        client.claim.assert_called_once()
    def test_ordinary_waiting_is_not_parked(self):
        from optimization_worker.image_service import review_parked
        store=Mock(); store.inspect.return_value={'state':'waiting', 'reason':'unknown'}
        self.assertFalse(review_parked(store, {'request_id':'one', 'phase':'host_pending'}))
    def test_resumed_review_returns_to_original_worker_without_claim(self):
        from optimization_worker.image_service import review_parked
        active={'claim':{'id':1,'task_type':'image.generate'},'request_id':'one','phase':'host_pending'}
        client=Mock(); client.identity='test'
        store=Mock(); store.inspect.return_value={'state':'qc_pending','reason':'candidate_qc_unsettled'}
        journal=Mock(); journal.active.return_value=[active]
        provider=Mock(); provider.durable_session=True
        worker=Worker(client,'/tmp/unused',providers={'image.generate':provider},capabilities=('image.generate',),park=lambda r:review_parked(store,r))
        with patch('optimization_worker.runtime.Journal',return_value=journal), patch.object(worker,'_run',return_value='host_pending') as run:
            self.assertEqual(worker.once(),'host_pending')
        client.claim.assert_not_called()
        self.assertEqual(run.call_args.args[1],active)
        self.assertEqual(provider.synchronize.call_args_list[0].args[0],[active])
        self.assertEqual(provider.synchronize.call_args_list[-1].args[0],[active])
    def test_unconfirmed_target_color_yields_worker_slot(self):
        from optimization_worker.image_service import review_parked
        store=Mock(); store.inspect.return_value={'state':'waiting','reason':'target_color_unconfirmed'}
        self.assertTrue(review_parked(store, {'request_id':'one','phase':'host_pending'}))
