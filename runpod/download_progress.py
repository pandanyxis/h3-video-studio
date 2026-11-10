"""Plain line progress for container logs; counts come from transfer callbacks."""
import time
from tqdm import tqdm


def progress_line(label, received, total, elapsed, transferred):
    speed = transferred / elapsed if elapsed > 0 else 0
    amount = f'{received / 1024**3:.2f}'
    if total:
        amount += f'/{total / 1024**3:.2f} GiB ({received / total:.1%})'
    else:
        amount += ' GiB (total unknown)'
    eta = f'{max(total - received, 0) / speed:.0f}s' if total and speed else 'calculating'
    return f'[download] {label}: {amount} | {speed / 1024**2:.1f} MiB/s | ETA {eta}'


class LogProgress(tqdm):
    """Use HF/Xet byte counters, never the size of a preallocated output file."""
    def __init__(self, *args, **kwargs):
        self.log_started = time.monotonic()
        kwargs.update(mininterval=15, miniters=1, disable=False, leave=True)
        super().__init__(*args, **kwargs)

    def display(self, msg=None, pos=None):
        elapsed = time.monotonic() - self.log_started
        # Xet estimates compressed transfer totals; only reconstruction has an exact total.
        total = None if self.desc.endswith('downloading bytes') else self.total
        print(progress_line(self.desc, self.n, total, elapsed,
                            max(self.n - self.initial, 0)), flush=True)
