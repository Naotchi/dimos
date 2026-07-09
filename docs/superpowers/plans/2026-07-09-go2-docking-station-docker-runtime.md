# Go2 Docking Station Docker Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Jetson-targeted Docker runtime path that can build from a cloned DimOS repo and run `dimos --viewer none run unitree-go2-basic --robot-ip <GO2_IP>` on the Go2 docking station.

**Architecture:** Add one self-contained runtime Dockerfile under `docker/` and verify it with non-Docker pytest asset checks. Update the Go2 platform docs with the exact build and run commands for the new runtime path, without changing `unitree-go2-basic` itself.

**Tech Stack:** Docker, Ubuntu 22.04, Python 3.12, uv, pytest, Markdown docs

## Global Constraints

- Target host: Jetson Orin NX docking station
- Target host OS: Ubuntu 20.04 based vendor image
- Target runtime: Docker container with Ubuntu 22.04 userspace
- Target blueprint: `unitree-go2-basic`
- Target viewer mode: `--viewer none`
- Use `uv sync --extra unitree` from the cloned checkout
- Support build-time arguments `DIMOS_REPO` and `DIMOS_REF`
- First supported runtime mode is foreground execution only
- Require `--network host` for the initial supported setup
- Do not change `unitree-go2-basic` unless the container path exposes a real bug
- Do not add new Docker-specific blueprint logic in v1
- Keep the docs short and operational

---

## File Structure

- Create `docker/go2-runtime/Dockerfile`
  - Purpose: Build a Jetson-friendly Ubuntu 22.04 runtime image that clones this repo and runs `uv sync --extra unitree`
- Create `dimos/core/tests/test_go2_runtime_docker_assets.py`
  - Purpose: Non-Docker smoke tests that pin the Dockerfile contract and docs contract
- Modify `docs/platforms/quadruped/go2/index.md`
  - Purpose: Add the shortest viable Docker build/run instructions for docking-station usage

### Task 1: Add Docker Runtime Asset Contract Tests

**Files:**
- Create: `dimos/core/tests/test_go2_runtime_docker_assets.py`

**Interfaces:**
- Consumes: `Path.read_text()` for `docker/go2-runtime/Dockerfile` and `docs/platforms/quadruped/go2/index.md`
- Produces: `test_go2_runtime_dockerfile_contains_required_runtime_contract() -> None`, `test_go2_docker_docs_include_build_and_run_commands() -> None`

- [ ] **Step 1: Write the failing test**

```python
# dimos/core/tests/test_go2_runtime_docker_assets.py
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "docker" / "go2-runtime" / "Dockerfile"
GO2_DOC = REPO_ROOT / "docs" / "platforms" / "quadruped" / "go2" / "index.md"


def test_go2_runtime_dockerfile_contains_required_runtime_contract() -> None:
    text = DOCKERFILE.read_text()

    assert "ARG FROM_IMAGE=ubuntu:22.04" in text
    assert "ARG DIMOS_REPO=" in text
    assert "ARG DIMOS_REF=" in text
    assert "git clone --branch" in text
    assert "uv sync --extra unitree" in text
    assert 'ENV UV_PROJECT_ENVIRONMENT="/opt/dimos/.venv"' in text
    assert 'WORKDIR /opt/dimos' in text
    assert 'CMD ["bash"]' in text


def test_go2_docker_docs_include_build_and_run_commands() -> None:
    text = GO2_DOC.read_text()

    assert "Docker on the Docking Station" in text
    assert "docker build -f docker/go2-runtime/Dockerfile" in text
    assert "--build-arg DIMOS_REPO=" in text
    assert "--build-arg DIMOS_REF=" in text
    assert "docker run --rm -it --network host" in text
    assert "dimos --viewer none run unitree-go2-basic --robot-ip" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest dimos/core/tests/test_go2_runtime_docker_assets.py -v`

Expected: FAIL with `FileNotFoundError` for `docker/go2-runtime/Dockerfile` and missing Docker doc assertions.

- [ ] **Step 3: Commit the failing test**

```bash
git add dimos/core/tests/test_go2_runtime_docker_assets.py
git commit -m "test: add go2 runtime docker asset contract"
```

### Task 2: Implement the Jetson Runtime Dockerfile

**Files:**
- Create: `docker/go2-runtime/Dockerfile`
- Test: `dimos/core/tests/test_go2_runtime_docker_assets.py`

**Interfaces:**
- Consumes: Task 1 test expectations
- Produces: A buildable Docker asset at `docker/go2-runtime/Dockerfile` with build args `FROM_IMAGE`, `DIMOS_REPO`, `DIMOS_REF`

- [ ] **Step 1: Write the minimal Dockerfile implementation**

```dockerfile
ARG FROM_IMAGE=ubuntu:22.04
FROM ${FROM_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG DIMOS_REPO=https://github.com/dimensionalOS/dimos.git
ARG DIMOS_REF=main

RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    ca-certificates \
    curl \
    git \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT="/opt/dimos/.venv"
ENV PATH="/root/.local/bin:/opt/dimos/.venv/bin:${PATH}"

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

RUN git clone --branch "${DIMOS_REF}" --single-branch "${DIMOS_REPO}" /opt/dimos

WORKDIR /opt/dimos

RUN uv sync --extra unitree

CMD ["bash"]
```

- [ ] **Step 2: Run the targeted test to verify it passes**

Run: `uv run pytest dimos/core/tests/test_go2_runtime_docker_assets.py::test_go2_runtime_dockerfile_contains_required_runtime_contract -v`

Expected: PASS

- [ ] **Step 3: Review for avoidable scope creep**

Confirm the Dockerfile does **not**:
- install CUDA-specific packages
- hardcode a Go2 IP
- set a hardcoded `ENTRYPOINT` for `dimos run`
- modify repo source files during build

No code change is needed if all four checks are true.

- [ ] **Step 4: Commit**

```bash
git add docker/go2-runtime/Dockerfile dimos/core/tests/test_go2_runtime_docker_assets.py
git commit -m "feat: add go2 docking runtime dockerfile"
```

### Task 3: Add Minimal Docking-Station Docker Docs

**Files:**
- Modify: `docs/platforms/quadruped/go2/index.md`
- Test: `dimos/core/tests/test_go2_runtime_docker_assets.py`

**Interfaces:**
- Consumes: Task 1 doc assertions, Task 2 Dockerfile path and build args
- Produces: A short doc section titled `Docker on the Docking Station` with exact build and run commands

- [ ] **Step 1: Add the Docker section to the Go2 platform doc**

Insert this section after `### Ready to run DimOS` and before `### What's Running`:

```md
### Docker on the Docking Station

If the docking station host image is older than the recommended Ubuntu version, run DimOS in a container with Ubuntu 22.04 userspace.

Build the runtime image on the docking station host:

```bash
docker build -f docker/go2-runtime/Dockerfile \
  --build-arg DIMOS_REPO=https://github.com/dimensionalOS/dimos.git \
  --build-arg DIMOS_REF=main \
  -t dimos-go2-runtime .
```

Run the minimal Go2 blueprint with host networking:

```bash
docker run --rm -it --network host dimos-go2-runtime \
  dimos --viewer none run unitree-go2-basic --robot-ip <YOUR_GO2_IP>
```
```

- [ ] **Step 2: Run the targeted test to verify the docs contract passes**

Run: `uv run pytest dimos/core/tests/test_go2_runtime_docker_assets.py::test_go2_docker_docs_include_build_and_run_commands -v`

Expected: PASS

- [ ] **Step 3: Run the full asset test file**

Run: `uv run pytest dimos/core/tests/test_go2_runtime_docker_assets.py -v`

Expected: `2 passed`

- [ ] **Step 4: Commit**

```bash
git add docs/platforms/quadruped/go2/index.md dimos/core/tests/test_go2_runtime_docker_assets.py
git commit -m "docs: add go2 docking docker runtime instructions"
```

### Task 4: Build-Time Verification on Development Host

**Files:**
- Modify: `docker/go2-runtime/Dockerfile` only if Task 3 test passes but local Docker build exposes an asset bug

**Interfaces:**
- Consumes: Task 2 Dockerfile and Task 3 docs
- Produces: A verified local build command and any minimal Dockerfile fix needed to make the build command valid

- [ ] **Step 1: Build the image locally**

Run:

```bash
docker build -f docker/go2-runtime/Dockerfile \
  --build-arg DIMOS_REPO=https://github.com/dimensionalOS/dimos.git \
  --build-arg DIMOS_REF=main \
  -t dimos-go2-runtime .
```

Expected: Docker build completes successfully, or fails with an environment-specific issue outside repo control.

- [ ] **Step 2: If the build fails due to a repo-owned Dockerfile mistake, fix only that mistake**

Allowed fixes:
- missing apt package
- invalid shell syntax
- incorrect `uv sync` invocation
- missing `PATH` / environment setup

Not allowed in this task:
- broad redesign
- changing blueprint behavior
- adding nonessential runtime features

- [ ] **Step 3: Re-run the build if Step 2 changed the Dockerfile**

Run the same build command again.

Expected: Build succeeds, or the remaining failure is clearly host-specific.

- [ ] **Step 4: Commit only if Step 2 changed tracked files**

```bash
git add docker/go2-runtime/Dockerfile
git commit -m "fix: make go2 runtime docker build valid"
```

If no tracked files changed, skip this commit.

## Self-Review

- Spec coverage:
  - Docker runtime image: covered by Task 2
  - Build args `DIMOS_REPO` and `DIMOS_REF`: covered by Tasks 1 and 2
  - Minimal build/run docs: covered by Task 3
  - Foreground `--viewer none` runtime path: covered by Task 3
  - No blueprint changes in v1: enforced by Tasks 2 and 4
- Placeholder scan:
  - No `TODO` / `TBD` / "appropriate handling" placeholders remain
- Type consistency:
  - Test names, file paths, and Dockerfile contract strings are consistent across all tasks

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-go2-docking-station-docker-runtime.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
