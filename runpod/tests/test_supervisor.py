"""Verify that a Qwen crash leaves active Studio and ComfyUI services running."""
import os
import signal
import unittest
from unittest.mock import MagicMock, patch

import supervisor


class SupervisorTests(unittest.TestCase):
    def test_qwen_restarts_after_studio_is_ready(self):
        processes = []
        handlers = {}

        def start(command, *, start_new_session):
            process = MagicMock(args=command, pid=len(processes) + 1)
            process.poll.return_value = None
            processes.append(process)
            return process

        def sleep(_):
            if len(processes) == 4:
                processes[3].poll.return_value = -9
            else:
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        def kill_group(pid, _):
            processes[pid - 1].poll.return_value = 0

        with (
            patch.dict(os.environ, {"LLAMA_API_KEY": "x" * 32}),
            patch.object(supervisor, "enabled", return_value=True),
            patch.object(supervisor, "studio_ready", return_value=True),
            patch.object(supervisor.signal, "signal", side_effect=handlers.__setitem__),
            patch.object(supervisor.signal, "SIGKILL", 9, create=True),
            patch.object(supervisor.subprocess, "Popen", side_effect=start),
            patch.object(supervisor.time, "sleep", side_effect=sleep),
            patch.object(supervisor.os, "killpg", create=True, side_effect=kill_group) as kill,
        ):
            self.assertEqual(supervisor.main(), 0)
        self.assertEqual(len(processes), 5)
        self.assertEqual(processes[3].args, processes[4].args)
        self.assertEqual([call.args[0] for call in kill.call_args_list if call.args[1] == signal.SIGTERM],
                         [1, 2, 3, 5])
