# Minimal runtime for the zenoh container PoC: the repo, its .venv, and the
# uv-managed Python are bind-mounted from the host; this image only provides
# the system shared libraries the compiled wheels need (lcm -> glib, cv2 -> GL).
FROM ubuntu:22.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libglib2.0-0 libpcre3 libgl1 libegl1 libgomp1 libx11-6 \
    && rm -rf /var/lib/apt/lists/*
