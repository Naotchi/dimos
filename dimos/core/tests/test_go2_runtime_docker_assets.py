from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO_ROOT / "docker" / "go2-runtime" / "Dockerfile"
GO2_DOC = REPO_ROOT / "docs" / "platforms" / "quadruped" / "go2" / "index.md"


def test_go2_runtime_dockerfile_contains_required_runtime_contract() -> None:
    text = DOCKERFILE.read_text()

    assert "ARG FROM_IMAGE=ubuntu:22.04" in text
    assert "ARG DIMOS_REPO=" in text
    assert "ARG DIMOS_REF=" in text
    assert "build-essential" in text
    assert "portaudio19-dev" in text
    assert "git clone --branch" in text
    assert "uv sync --extra unitree" in text
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
    assert "dimos --viewer none run unitree-go2-basic --robot-ip" in text
