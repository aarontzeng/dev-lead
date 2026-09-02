# test_scripts.py is a standalone script, not a pytest suite: it passes its own
# fixtures positionally from main() and prints its own results
# (`python3 scripts/test_scripts.py`). Collected by pytest instead, every test
# errors at setup with "fixture 'tmp' not found" — which reads exactly like a
# broken test suite, and was reported as one before anyone read the docstring
# two lines into the file. Ignoring it here makes `pytest` at the repo root
# quiet rather than misleading.
collect_ignore = ["test_scripts.py"]
