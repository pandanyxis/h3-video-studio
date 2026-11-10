import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch


class AutoUnloadTests(unittest.TestCase):
    def setUp(self):
        class Queue:
            def __init__(self):
                self.mutex = threading.RLock()
                self.remaining = 1
                self.flags = {}
                self.completed = []

            def task_done(self, item, status=None):
                self.completed.append((item, status))
                self.remaining -= 1
                return 'saved'

            def get_tasks_remaining(self):
                return self.remaining

            def set_flag(self, name, value):
                self.flags[name] = value

        self.queue_class = Queue
        execution = types.ModuleType('execution')
        execution.PromptQueue = Queue
        path = Path(__file__).resolve().parents[1] / 'auto-unload/__init__.py'
        spec = importlib.util.spec_from_file_location('auto_unload', path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'execution': execution}):
            spec.loader.exec_module(self.module)

    def test_cleanup_after_final_prompt_including_failure(self):
        for status in ('success', 'error', 'interrupted'):
            with self.subTest(status=status):
                queue = self.queue_class()
                self.assertEqual(queue.task_done('prompt', status=status), 'saved')
                self.assertEqual(queue.completed, [('prompt', status)])
                self.assertEqual(queue.flags, {'unload_models': True, 'free_memory': True})

    def test_queued_batch_keeps_models_until_last_prompt(self):
        queue = self.queue_class()
        queue.remaining = 2
        queue.task_done('first')
        self.assertEqual(queue.flags, {})
        queue.task_done('last')
        self.assertTrue(queue.flags['free_memory'])

    def test_install_is_idempotent(self):
        wrapper = self.queue_class.task_done
        self.module.install(self.queue_class)
        self.assertIs(self.queue_class.task_done, wrapper)
