"""Rerun original review with corrected exit2 expectations, without editing its
historical characterization or receipts. Reuse identical race scheduling/mutations."""
import hashlib
import json
from pathlib import Path

original = Path(__file__).with_name('review.py')
source = original.read_text()
start = "        code, result, error, current_sha = race(command, 7, lw)\n"
stop = "        # Oversized pair: individually bounded/valid workloads, but response\n"
assert source.count(start) == source.count(stop) == 1
replacement = '''        code, result, error, current_sha = race(command, 7, lw)
        assert code == 2 and result is None and ('final raw source generation changed' in error or 'byte bound exceeded' in error), (code, error)
        receipts['F1_late_workload_generation_corrected'] = dict(exit_code=code, error=error.strip())
        code, result, error, current_sha = race(coarse_command, 5, run_path)
        assert code == 2 and result is None and ('final raw source generation changed' in error or 'byte bound exceeded' in error), (code, error)
        receipts['F1_target_during_workload_fence_corrected'] = dict(exit_code=code, error=error.strip())
'''
begin, end = source.index(start), source.index(stop)
corrected = source[:begin] + replacement + source[end:]
print(json.dumps(dict(original_review_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
                      mode='same original probes; corrected late-window exit2 expectations')), flush=True)
exec(compile(corrected, str(original), 'exec'), dict(__file__=str(original), __name__='__main__'))
