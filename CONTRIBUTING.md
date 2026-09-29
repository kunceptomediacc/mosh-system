# Contributing to MOSH

Thanks for helping improve MOSH. The project favors small, auditable changes that preserve its local-first and approval-gated safety model.

## Before opening a change

1. Search existing issues and pull requests for related work.
2. Keep the change focused on one behavior or documentation concern.
3. Never include credentials, identity state, local databases, generated reports, or private handoff notes.
4. Preserve the scanner-level exclusion for `D:\balot\thor\`.
5. Do not add an external side effect without an explicit approval contract, durable audit record, replay protection, and fail-closed tests.

## Local setup

```powershell
$env:PYTHONPATH = (Resolve-Path '.\apps\mosh-core').Path
python -m pip install -e '.\apps\mosh-core'
python -m pip install -r '.\apps\mosh-core\requirements-identity.txt'
```

## Tests

Run the complete Python suite before submitting a pull request:

```powershell
$env:PYTHONPATH = (Resolve-Path '.\apps\mosh-core').Path
python -m unittest discover -s '.\apps\mosh-core\tests' -p 'test_*.py'
```

GitHub Actions repeats the suite on supported Python versions. Pull requests should not be merged while CI is failing.

## Commit and pull-request guidance

- Use a clear conventional commit such as `feat:`, `fix:`, `test:`, `docs:`, or `ci:`.
- Explain what changed, why it is safe, and how it was verified.
- Include tests for behavioral changes and update contracts or operational notes when applicable.
- Call out any new local state, network access, credentials, permissions, or external effects.
- Avoid unrelated formatting or generated-file changes.

## Safety and privacy

Use synthetic data in tests and examples. Do not submit personal information, access tokens, API keys, cookies, private URLs, or machine-specific account details. If a report may expose a vulnerability or secret, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

