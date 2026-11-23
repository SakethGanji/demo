# Broken template bundles

Each directory is a small `wf-template/1` bundle that is wrong in exactly one way. `_expected.json` maps the directory
to the code the validator must report (`OK` means the bundle is valid and only fails when installed:
`rollback-on-publish` carries a failing approved test in its sub-workflow, so the install must roll back completely).
Used by `tests/unit/test_stage8_b5_bundle.py` and available to the CI gate for its negative cases.
