# Releasing to PyPI

The package is `dsajudge` (console script `dsa`). The name was unclaimed on PyPI when this was
written (2026-10-15); check again before the first upload, and rename in `pyproject.toml` if it
was taken (`dsa-judge` was also free).

## One-time setup

1. On PyPI (and, to rehearse, on TestPyPI) add a **trusted publisher** for the project, using
   "Add a pending publisher" if the project does not exist yet:
   - owner `PkiPzi`, repository `dsa-judge`
   - workflow file `publish.yml`
   - environment `pypi` (TestPyPI: `testpypi`)
2. In the GitHub repository settings create the environments `pypi` and `testpypi`. Adding a
   required reviewer to `pypi` makes every release wait for your click.

No API token is created or stored: the workflow gets a short-lived OIDC credential.

## Cutting a release

1. Bump `__version__` in `src/dsajudge/__init__.py` (the only place; hatch reads it).
2. Merge to `main`. CI's `package` job already builds the wheel and runs
   `scripts/smoke_wheel.sh` on it: clean venv, `dsa doctor`, the bundled sample problems, a
   function-mode solve, a design-problem build and solve.
3. Rehearse (optional): Actions -> Publish -> Run workflow -> `testpypi`, then
   `uv tool install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ dsajudge`.
4. Tag and push: `git tag v0.4.0 && git push origin v0.4.0`. The workflow refuses a tag that
   does not match `__version__`, reruns ruff, mypy and pytest, builds, smoke-tests the wheel and
   uploads it.

Installing as a user: `uv tool install dsajudge`, or `uv tool install 'dsajudge[ai]'` for local
photo and voice intake. The sample problems `max-subarray`, `min-stack` and `lru-cache` are
bundled, so `dsa open max-subarray` works straight away.

## What is in the wheel

The package, the code-generation templates (including `*.inc` partials), the prompts, and the
public sample problems under `dsajudge/_fixtures/problems/`. Tests, golden files and solution
variants are only in the sdist.
