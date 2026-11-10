import contextlib
import io
import unittest
from download_progress import LogProgress, progress_line


class DownloadProgressTests(unittest.TestCase):
    def test_percentage_speed_and_eta_use_transferred_bytes(self):
        line = progress_line('model', 3 * 1024**3, 8 * 1024**3, 10, 1024**3)
        self.assertIn('3.00/8.00 GiB (37.5%)', line)
        self.assertIn('102.4 MiB/s', line)
        self.assertIn('ETA 50s', line)

    def test_unknown_total_and_zero_speed_do_not_invent_eta(self):
        self.assertIn('total unknown', progress_line('model', 0, None, 0, 0))
        self.assertIn('ETA calculating', progress_line('model', 10, 100, 10, 0))

    def test_callback_updates_emit_lines_and_preserve_resume_offset(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            bar = LogProgress(desc='model', total=100, initial=20)
            bar.update(30)
            bar.refresh()
            bar.close()
        self.assertIn('(50.0%)', output.getvalue())
        self.assertNotIn('\r', output.getvalue())

    def test_long_transfers_report_before_completion(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            bar = LogProgress(desc='model', total=100)
            # tqdm uses its clock for rate limiting; simulate 16 elapsed seconds.
            bar.last_print_t -= 16
            bar.start_t -= 16
            bar.update(25)
            self.assertIn('(25.0%)', output.getvalue())
            bar.close()

    def test_xet_estimated_network_total_is_not_presented_as_exact(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            bar = LogProgress(desc='model: downloading bytes', total=100)
            bar.update(25)
            bar.refresh()
            bar.close()
        self.assertIn('total unknown', output.getvalue())
        self.assertNotIn('(25.0%)', output.getvalue())
