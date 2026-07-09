# Final Fix 3 Report

## Scope

Addressed the remaining final-review findings for the Go2 docking-station Docker runtime branch within the allowed files:

- `docker/go2-runtime/Dockerfile`
- `dimos/core/tests/test_go2_runtime_docker_assets.py`
- `docs/platforms/quadruped/go2/index.md`

Left the unrelated local `uv.lock` change untouched, unstaged, and uncommitted.

## Changes Made

### 1. Docker runtime dependency install contract

Updated the Docker runtime image sync command in `docker/go2-runtime/Dockerfile` from:

```dockerfile
RUN uv sync --extra unitree
```

to:

```dockerfile
RUN uv sync --extra unitree --no-default-groups --locked
```

This fixes both review findings:

- prevents installation of default dependency groups
- enforces the checked-in lockfile during image build

### 2. Asset contract test tightening

Updated `dimos/core/tests/test_go2_runtime_docker_assets.py` so it now asserts the exact required `uv sync` invocation instead of the looser substring check.

Old assertion:

```python
assert "uv sync --extra unitree" in text
```

New assertion:

```python
assert "RUN uv sync --extra unitree --no-default-groups --locked" in lines
```

This makes the test fail if either required flag is removed or if the sync command regresses away from the reviewed contract.

### 3. Minor docs clarification

Added one sentence in `docs/platforms/quadruped/go2/index.md` clarifying that the `What's Running` table refers to the standard `dimos run unitree-go2` stack rather than the minimal `unitree-go2-basic` Docker example immediately above it.

## TDD / Verification Evidence

### Red

After tightening the asset test, I ran:

```bash
uv run pytest dimos/core/tests/test_go2_runtime_docker_assets.py -q
```

Result:

- test failed as expected
- failure was on the missing exact line:
  `RUN uv sync --extra unitree --no-default-groups --locked`

### Green

After updating the Dockerfile and doc, I reran:

```bash
uv run pytest dimos/core/tests/test_go2_runtime_docker_assets.py -q
```

Result:

- `2 passed in 0.02s`

## Notes

- `uv run` emitted an existing settings-discovery warning about parsing `exclude-newer = "7 days"` from `pyproject.toml`, but pytest still executed normally and the focused test file passed.
- No other tracked files were modified.
