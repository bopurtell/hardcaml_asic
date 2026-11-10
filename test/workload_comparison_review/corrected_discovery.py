"""Preserve F2 characterization; run its original mutations expecting rejection,
plus an existing child whose postcheck file is initially absent."""
import hashlib
import json
from pathlib import Path

original = Path(__file__).with_name('discovery_review.py')
source = original.read_text()
start = "                assert p.returncode == 0, (p.returncode, stderr.decode())\n"
stop = "            finally:\n"
assert source.count(start) == source.count(stop) == 1
replacement = '''                assert p.returncode == 2 and not stdout and b'postcheck discovery generation changed' in stderr, (p.returncode, stdout, stderr)
                current = clean()
                assert current['left']['outcomes']['postchecks'][0]['status'] == 'failed'
                receipts.append(dict(initial=preexisting_directory, mutation_open=5, exit_code=p.returncode,
                                     error=stderr.decode().strip(), clean_current_postchecks=current['left']['outcomes']['postchecks']))
'''
begin, end = source.index(start), source.index(stop)
corrected = source[:begin] + replacement + source[end:]
corrected = corrected.replace('for preexisting_directory in [False, True]:',
                              "for preexisting_directory in [False, True, 'child-without-postcheck']:")
corrected = corrected.replace('                checks.mkdir()\n',
                              "                checks.mkdir()\n            if preexisting_directory == 'child-without-postcheck':\n                (checks / 'new-failed-postcheck').mkdir()\n")
corrected = corrected.replace('new_check.mkdir(parents=True)', 'new_check.mkdir(parents=True, exist_ok=True)')
print(json.dumps(dict(original_discovery_sha256=hashlib.sha256(original.read_bytes()).hexdigest(),
                      mode='original absent/empty mutations with exit2 expectations plus existing-child case')), flush=True)
exec(compile(corrected, str(original), 'exec'), dict(__file__=str(original), __name__='__main__'))
