from __future__ import annotations

import os
from pathlib import Path

# RT-DETRv4 公式 checkpoint の Google Drive file id（COCO 学習済み）
_GDRIVE_IDS = {
    "s": "1jDAVxblqRPEWed7Hxm6GwcEl7zn72U6z",
    "m": "1O-YpP4X-quuOXbi96y2TKkztbjroP5mX",
    "l": "1shO9EzZvXZyKedE2urLsN4dwEv8Jqa_8",
    "x": "19gnkMTgFveJsrOvSmEPQXCTG6v9oQHN3",
}


def weights_dir() -> Path:
    base = os.environ.get("DIMOS_RTDETRV4_DIR")
    d = Path(base) if base else Path.home() / ".cache" / "dimos" / "rtdetrv4"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_config(size: str) -> Path:
    size = size.lower()
    if size not in _GDRIVE_IDS:
        raise ValueError(f"Unknown model size {size!r}; expected one of {sorted(_GDRIVE_IDS)}")
    import engine  # provided by Naotchi/rt-detrv4

    config = (
        Path(engine.__file__).resolve().parent.parent
        / "configs"
        / "rtv4"
        / f"rtv4_hgnetv2_{size}_coco.yml"
    )
    if not config.is_file():
        raise FileNotFoundError(
            f"RT-DETRv4 config not found at {config}. "
            "Is Naotchi/rt-detrv4 installed with configs shipped?"
        )
    return config


def resolve_weights(
    size: str,
    weights: str | os.PathLike[str] | None = None,
    download: bool = True,
) -> Path:
    size = size.lower()
    if weights is not None:
        p = Path(weights)
        if not p.is_file():
            raise FileNotFoundError(f"Weights not found: {p}")
        return p
    if size not in _GDRIVE_IDS:
        raise ValueError(f"Unknown model size {size!r}; expected one of {sorted(_GDRIVE_IDS)}")
    dest = weights_dir() / f"rtv4_hgnetv2_{size}_coco.pth"
    if dest.is_file():
        return dest
    if not download:
        raise FileNotFoundError(
            f"Weights not found at {dest}. Download Google Drive id "
            f"{_GDRIVE_IDS[size]} to that path, or pass weights=."
        )
    try:
        import gdown
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("gdown is required to download weights: `uv add gdown`") from e
    gdown.download(id=_GDRIVE_IDS[size], output=str(dest), quiet=False)
    if not dest.is_file():  # pragma: no cover
        raise RuntimeError(f"Download failed; expected file at {dest}")
    return dest
