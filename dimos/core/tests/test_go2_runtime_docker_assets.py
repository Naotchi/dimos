from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "docker" / "go2-runtime" / "Dockerfile"
GO2_DOC = REPO_ROOT / "docs" / "platforms" / "quadruped" / "go2" / "index.md"


def _dockerfile_packages(text: str) -> set[str]:
    packages: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("ARG ", "FROM ", "RUN ", "ENV ", "WORKDIR ", "CMD ")):
            continue
        if stripped.startswith(("&&", "#")):
            continue

        package = stripped.removesuffix("\\").strip()
        if package:
            packages.add(package)

    return packages


def test_go2_runtime_dockerfile_contains_required_runtime_contract() -> None:
    text = DOCKERFILE.read_text()
    packages = _dockerfile_packages(text)
    lines = {line.strip() for line in text.splitlines()}

    assert "ARG FROM_IMAGE=ubuntu:22.04" in text
    assert "ARG DIMOS_REPO=" in text
    assert "ARG DIMOS_REF=" in text
    assert {"build-essential", "gnupg2", "libgfortran5", "portaudio19-dev"} <= packages
    assert {"libgl1", "libgl1-mesa-dri"} <= packages
    assert "libturbojpeg0-dev" in packages
    assert "git clone --branch" in text
    assert "RUN uv sync --extra unitree --no-default-groups" in lines
    assert 'ENV UV_PROJECT_ENVIRONMENT="/opt/dimos/.venv"' in text
    assert "WORKDIR /opt/dimos" in text
    assert 'CMD ["bash"]' in text


def test_go2_docker_docs_include_build_and_run_commands() -> None:
    text = GO2_DOC.read_text()

    assert "Docker on the Docking Station" in text
    assert "docker build -f docker/go2-runtime/Dockerfile" in text
    assert "--build-arg DIMOS_REPO=" in text
    assert "--build-arg DIMOS_REF=" in text
    assert "docker run --rm -it --network host" in text
    assert "dimos --robot-ip <YOUR_GO2_IP> --viewer none run unitree-go2-basic" in text
