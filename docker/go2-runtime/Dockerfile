ARG FROM_IMAGE=ubuntu:22.04
FROM ${FROM_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG DIMOS_REPO=https://github.com/dimensionalOS/dimos.git
ARG DIMOS_REF=main

RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    build-essential \
    ca-certificates \
    curl \
    git \
    gnupg2 \
    libgfortran5 \
    libgl1 \
    libgl1-mesa-dri \
    libturbojpeg0-dev \
    portaudio19-dev \
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

RUN uv sync --extra unitree --no-default-groups

CMD ["bash"]
