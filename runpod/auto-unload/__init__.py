"""Request ComfyUI's worker-thread cleanup after the final queued prompt."""
import functools
import logging

from execution import PromptQueue


def install(queue_class):
    original = queue_class.task_done
    if getattr(original, '_combined_auto_unload', False):
        return

    @functools.wraps(original)
    def task_done(self, *args, **kwargs):
        # PromptQueue uses an RLock. Keep completion and the idle check atomic
        # with respect to submissions; never free tensors from the HTTP thread.
        with self.mutex:
            result = original(self, *args, **kwargs)
            if self.get_tasks_remaining() == 0:
                self.set_flag('unload_models', True)
                self.set_flag('free_memory', True)
                logging.info('[auto-unload] Queue finished; requesting model unload and execution cache cleanup')
            return result

    task_done._combined_auto_unload = True
    queue_class.task_done = task_done


install(PromptQueue)
NODE_CLASS_MAPPINGS = {}
