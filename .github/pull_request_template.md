## What does this change?

<!-- A short description, and the issue it fixes if there is one (for example: Fixes #12). -->

## Checklist

- [ ] `python3 -m unittest discover tests` passes
- [ ] I ran the app (or `python3 safe-disk-cleaner --report`) and it still works
- [ ] If I changed `helper.py` or `policy.py`: every input is checked against a whitelist or a strict
      pattern, the system's current state is re-checked, and I added a test that tries to misuse the
      new code (see CONTRIBUTING.md)
