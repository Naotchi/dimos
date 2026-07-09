# Go2 Docking Station Docker Runtime Design

## Goal

Enable running `dimos --viewer none run unitree-go2-basic --robot-ip <GO2_IP>` inside Docker on the Go2 docking station's Jetson Orin NX.

This spec is intentionally narrow:
- Target host: Jetson Orin NX docking station
- Target host OS: Ubuntu 20.04 based vendor image
- Target runtime: Docker container with Ubuntu 22.04 userspace
- Target blueprint: `unitree-go2-basic`
- Target viewer mode: `--viewer none`

Out of scope:
- `unitree-go2`
- agentic blueprints
- perception / VLM / CUDA optimization
- viewer rendering inside the container
- converting the blueprint itself into a `DockerModule`

## Problem

The docking station host is `aarch64` and Ubuntu 20.04 based. DimOS documentation and expected runtime are centered on newer userspace assumptions, while the current repo does not provide a straightforward "clone repo and run inside one Docker container on Jetson" path.

There is already infrastructure for Dockerized modules, but not a minimal full-repo runtime image for running an existing blueprint directly with `dimos run`.

## Chosen Approach

Add a minimal Jetson-targeted runtime Docker image that:

1. Uses `ubuntu:22.04` as container userspace.
2. Installs Python 3.12 and `uv`.
3. Clones the DimOS repository during `docker build`.
4. Runs `uv sync --extra unitree` from the cloned checkout.
5. Leaves execution command selection to `docker run`, so the first target command is:
   `dimos --viewer none run unitree-go2-basic --robot-ip <GO2_IP>`.

This keeps the implementation focused on "make the current blueprint runnable in Docker" rather than introducing new blueprint abstractions.

## Alternatives Considered

### 1. Use `uv pip install -e '.[unitree]'` after clone

Pros:
- Simple mental model
- Works directly from the cloned checkout

Cons:
- Weaker reproducibility than `uv sync`
- Ignores the existing `uv.lock` as the primary environment definition

Rejected because reproducibility matters more than minimal command length.

### 2. Extend the existing `docker/python` or `docker/dev` images

Pros:
- Reuses existing Docker image hierarchy
- More consistent with current repo Docker organization

Cons:
- Current images are not optimized for this exact runtime goal
- Adds more moving parts than necessary for the first working path

Rejected for the first iteration because it is a larger design surface than needed.

### 3. Create a dedicated Dockerized blueprint or `DockerModule`

Pros:
- More explicit long-term abstraction
- Could integrate better with other deployment workflows later

Cons:
- Unnecessary architectural weight for current goal
- Solves a broader problem than "run an existing blueprint in a container"

Rejected as premature.

## Deliverables

### 1. Runtime Dockerfile

Add a new Dockerfile for Jetson-hosted runtime usage.

Expected properties:
- Base image: `ubuntu:22.04`
- Installs git, curl, Python 3.12, and required system packages for Python + DimOS runtime
- Installs `uv`
- Clones a configurable repo URL and branch at build time
- Runs `uv sync --extra unitree`
- Sets `PATH` so `uv run` / virtualenv-backed commands are available
- Defaults to a shell or neutral command, not a hardcoded Go2 command

Build-time arguments:
- `DIMOS_REPO`
- `DIMOS_REF`

These allow testing forks and feature branches without editing the Dockerfile.

### 2. Minimal run instructions

Add short docs showing:

Build:
```bash
docker build -f <new-dockerfile> --build-arg DIMOS_REPO=... --build-arg DIMOS_REF=... -t dimos-go2-runtime .
```

Run:
```bash
docker run --rm -it --network host dimos-go2-runtime \
  dimos --viewer none run unitree-go2-basic --robot-ip <GO2_IP>
```

The first supported runtime mode is foreground execution only.

## Runtime Assumptions

- Docker is already available on the Jetson host.
- The host can already reach the Go2 IP with low latency.
- `--network host` is required for the initial supported setup.
- The container does not need to expose web UI ports for this phase.
- GPU access is not required for the initial success condition.

## Error Handling and Failure Modes

The implementation should make the likely failure points obvious:

- `git clone` failure:
  surface normal Docker build error output.
- Python 3.12 package availability failure on Ubuntu 22.04:
  fail the build early and explicitly.
- `uv sync --extra unitree` dependency resolution failure:
  fail the build; do not add fallback installers in v1.
- runtime connection failure to Go2:
  rely on existing DimOS / WebRTC logs.

No fallback path is added in v1. The first version should be strict and easy to debug.

## Verification

Success requires both:

1. `docker build` completes successfully on the Jetson host.
2. `docker run --network host ... dimos --viewer none run unitree-go2-basic --robot-ip <GO2_IP>` starts and reaches Go2 connection startup without container-side dependency errors.

Nice-to-have but not required in v1:
- documented sample logs
- daemonized execution
- support for other blueprints

## Implementation Notes

- Do not change `unitree-go2-basic` itself unless the container path exposes a real bug.
- Do not add new Docker-specific blueprint logic in v1.
- Prefer adding one self-contained Docker runtime path over modifying the existing Docker module framework.
- Keep the docs short and operational.
