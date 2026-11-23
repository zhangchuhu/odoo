import unittest
from optimization_worker.interactive_dispatcher import research_prompt

class ActivationPromptTest(unittest.TestCase):
    def test_never_guess_and_stop_if_screenshot_unavailable(self):
        for action in ({'action':'browse_research','collector_plan':{'plan':{}}},{'action':'collect_candidate_images'}):
            prompt=research_prompt(action)
            target = '打开当前合格商品图库' if action['action'] == 'collect_candidate_images' else '切换到已绑定任务标签'
            self.assertIn(target,prompt)
            self.assertIn('不得通过窗口标题猜测',prompt)
            self.assertIn('截图不可用时暂停',prompt)


    def test_mercado_screens_before_visual_and_recaptures_only_when_needed(self):
        prompt = research_prompt({'action':'browse_research','collector_plan':{'plan':{}}})
        for term in ('核验销量并保存未达门槛判定', '通过门槛才返回详情视觉核验',
                     '独立调用 app.getScreenshot()', '仅再试一次', '截图不可用时暂停视觉核验'):
            self.assertIn(term, prompt)
