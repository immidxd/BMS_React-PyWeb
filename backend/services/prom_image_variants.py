"""Prom-only image derivatives that remain safe after Shafa's thumbnail crop.

The source image is never modified.  A content-addressed WebP derivative is
created in a separate cache and uploaded under a separate immutable R2 key.
Every failure is deliberately fail-open: Prom receives the original URL.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import statistics
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit

import requests
from PIL import Image, ImageChops, ImageOps

try:
    from backend.services import product_images, r2_storage
except ImportError:  # app can run with backend/ on PYTHONPATH
    from services import product_images, r2_storage

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "v1"
_SHAFA_THUMB_RATIO = 310 / 430
_VISIBLE_MARGIN = 0.04
SAFE_LEFT = (1 - _SHAFA_THUMB_RATIO) / 2 + _VISIBLE_MARGIN * _SHAFA_THUMB_RATIO
SAFE_RIGHT = 1 - SAFE_LEFT
SAFE_TOP = 0.04
SAFE_BOTTOM = 0.96
_MAX_ANALYSIS_SIDE = 384
_MAX_BORDER_SPREAD = 30

_lock_guard = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


@dataclass(frozen=True)
class VariantResult:
    url: str
    applied: bool
    reason: str


def _feature_enabled() -> bool:
    return os.getenv("PROM_SHAFA_SAFE_MAIN_IMAGE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _lock_for(digest: str) -> threading.Lock:
    with _lock_guard:
        return _key_locks.setdefault(digest, threading.Lock())


@lru_cache(maxsize=2048)
def _content_digest(path: str, mtime_ns: int, size: int) -> str:
    h = hashlib.sha256()
    h.update(ALGORITHM_VERSION.encode("ascii"))
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:24]


def _source_from_public_url(public_url: str) -> Optional[Tuple[str, Path]]:
    """Map an immutable public R2 URL back to its local master safely."""
    base = (r2_storage.R2_PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        return None
    source_url, base_url = urlsplit(public_url), urlsplit(base)
    if (source_url.scheme, source_url.netloc) != (base_url.scheme, base_url.netloc):
        return None
    prefix = base_url.path.rstrip("/") + "/"
    if not source_url.path.startswith(prefix):
        return None
    key = unquote(source_url.path[len(prefix):])
    posix_key = PurePosixPath(key)
    if not key or posix_key.is_absolute() or ".." in posix_key.parts:
        return None
    root = Path(product_images.get_images_dir()).expanduser().resolve()
    source = root.joinpath(*posix_key.parts).resolve()
    try:
        source.relative_to(root)
    except ValueError:
        return None
    return key, source


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    return values[min(len(values) - 1, round((len(values) - 1) * fraction))]


def _analyze_content(image: Image.Image):
    """Return (background RGB, normalized bbox) for a uniform studio image."""
    probe = image.copy()
    probe.thumbnail((_MAX_ANALYSIS_SIDE, _MAX_ANALYSIS_SIDE), Image.Resampling.LANCZOS)
    width, height = probe.size
    if min(width, height) < 40:
        return None

    border_width = max(2, min(width, height) // 80)
    pixels = probe.load()
    border = []
    for y in range(height):
        for x in range(width):
            if x < border_width or x >= width - border_width or y < border_width or y >= height - border_width:
                border.append(pixels[x, y])
    background = tuple(int(statistics.median(channel)) for channel in zip(*border))
    border_delta = [max(abs(px[c] - background[c]) for c in range(3)) for px in border]
    if _percentile(border_delta, 0.90) > _MAX_BORDER_SPREAD:
        return None

    threshold = max(12, min(38, _percentile(border_delta, 0.95) + 8))
    difference = ImageChops.difference(probe, Image.new("RGB", probe.size, background))
    red, green, blue = difference.split()
    strongest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = strongest.point(lambda value: 255 if value > threshold else 0)
    if hasattr(mask, "get_flattened_data"):
        mask_pixels = list(mask.get_flattened_data())
    else:  # Pillow < 12
        mask_pixels = list(mask.getdata())
    content_count = sum(1 for value in mask_pixels if value)
    coverage = content_count / (width * height)
    if coverage < 0.003 or coverage > 0.75:
        return None

    col_counts = [0] * width
    row_counts = [0] * height
    for index, value in enumerate(mask_pixels):
        if value:
            x, y = index % width, index // width
            col_counts[x] += 1
            row_counts[y] += 1
    min_col_pixels = max(2, round(height * 0.008))
    min_row_pixels = max(2, round(width * 0.008))
    cols = [i for i, count in enumerate(col_counts) if count >= min_col_pixels]
    rows = [i for i, count in enumerate(row_counts) if count >= min_row_pixels]
    if not cols or not rows:
        return None

    pad_x, pad_y = max(1, round(width * 0.008)), max(1, round(height * 0.008))
    x0, x1 = max(0, cols[0] - pad_x), min(width, cols[-1] + 1 + pad_x)
    y0, y1 = max(0, rows[0] - pad_y), min(height, rows[-1] + 1 + pad_y)
    if (x1 - x0) / width < 0.12 or (y1 - y0) / height < 0.08:
        return None
    return background, (x0 / width, y0 / height, x1 / width, y1 / height)


def _render_safe_variant(source: Path, destination: Path) -> Tuple[bool, str]:
    with Image.open(source) as opened:
        transposed = ImageOps.exif_transpose(opened)
        if transposed.mode in ("RGBA", "LA") or "transparency" in transposed.info:
            rgba = transposed.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
        else:
            image = transposed.convert("RGB")

    width, height = image.size
    aspect = width / height
    if not 0.90 <= aspect <= 1.10:
        return False, "non-square"
    analysis = _analyze_content(image)
    if analysis is None:
        return False, "non-uniform-background"
    background, bbox = analysis

    side = max(width, height)
    base = Image.new("RGB", (side, side), background)
    base_x, base_y = (side - width) // 2, (side - height) // 2
    base.paste(image, (base_x, base_y))
    x0 = (base_x + bbox[0] * width) / side
    y0 = (base_y + bbox[1] * height) / side
    x1 = (base_x + bbox[2] * width) / side
    y1 = (base_y + bbox[3] * height) / side

    already_safe = (
        width == height
        and x0 >= SAFE_LEFT and x1 <= SAFE_RIGHT
        and y0 >= SAFE_TOP and y1 <= SAFE_BOTTOM
    )
    if already_safe:
        return False, "already-safe"

    subject_width, subject_height = x1 - x0, y1 - y0
    scale = min(1.0, (SAFE_RIGHT - SAFE_LEFT) / subject_width,
                (SAFE_BOTTOM - SAFE_TOP) / subject_height)
    if scale < 0.999:
        scale *= 0.985  # absorb detection/JPEG rounding at Shafa's crop boundary
    resized_side = max(1, round(side * scale))
    resized = base.resize((resized_side, resized_side), Image.Resampling.LANCZOS)
    center_x, center_y = (x0 + x1) * side / 2, (y0 + y1) * side / 2
    paste_x = round(side / 2 - center_x * scale)
    paste_y = round(side / 2 - center_y * scale)
    result = Image.new("RGB", (side, side), background)
    result.paste(resized, (paste_x, paste_y))

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp.webp")
    result.save(temporary, "WEBP", quality=88, method=4)
    os.replace(temporary, destination)
    return True, "rendered"


def _write_marker(path: Path, value: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def prepare_prom_main_image(original_url: str) -> VariantResult:
    """Return a Prom-only safe derivative, or the untouched original on any issue."""
    if not _feature_enabled():
        return VariantResult(original_url, False, "disabled")
    if not original_url or not r2_storage.is_enabled() or not r2_storage.R2_PUBLIC_BASE_URL:
        return VariantResult(original_url, False, "storage-unavailable")
    resolved = _source_from_public_url(original_url)
    if resolved is None:
        return VariantResult(original_url, False, "source-url-unmapped")
    _, source = resolved
    try:
        stat = source.stat()
        digest = _content_digest(str(source), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return VariantResult(original_url, False, "source-missing")

    derived_key = f"derived/prom-shafa-main/{ALGORITHM_VERSION}/{digest}.webp"
    derived_url = r2_storage.public_url(derived_key)
    if not derived_url:
        return VariantResult(original_url, False, "public-url-unavailable")
    cache_root = Path(os.getenv(
        "PROM_IMAGE_VARIANT_CACHE_DIR",
        str(Path(product_images.get_images_dir()) / ".derived" / "prom-shafa-main"),
    )).expanduser()
    output = cache_root / ALGORITHM_VERSION / f"{digest}.webp"
    uploaded = cache_root / ALGORITHM_VERSION / f"{digest}.uploaded"
    skipped = cache_root / ALGORITHM_VERSION / f"{digest}.skip"

    with _lock_for(digest):
        if uploaded.exists():
            return VariantResult(derived_url, True, "cached")
        if skipped.exists():
            return VariantResult(original_url, False, skipped.read_text(encoding="utf-8") or "skipped")
        try:
            if not output.exists():
                changed, reason = _render_safe_variant(source, output)
                if not changed:
                    _write_marker(skipped, reason)
                    return VariantResult(original_url, False, reason)
            r2_storage.upload_file(str(output), derived_key, content_type="image/webp")
            _write_marker(uploaded)
            return VariantResult(derived_url, True, "uploaded")
        except Exception as exc:
            logger.warning("Prom safe main image failed for %s: %s", source.name, exc)
            return VariantResult(original_url, False, "processing-failed")


def _prom_original_image_url(url: str) -> Optional[str]:
    parsed = urlsplit(str(url or ""))
    if parsed.scheme != "https" or parsed.hostname != "images.prom.ua":
        return None
    return re.sub(r"_w\d+_h\d+_", "_w0_h0_", url, count=1)


def prepare_prom_remote_main_image(original_url: str) -> VariantResult:
    """Create the same safe derivative from an existing Prom CDN master.

    This is reserved for legacy Prom listings whose old source is no longer in
    BMS/R2.  Only ``images.prom.ua`` is accepted, and the original Prom/BMS image
    is never overwritten.
    """
    if not _feature_enabled():
        return VariantResult(original_url, False, "disabled")
    if not r2_storage.is_enabled() or not r2_storage.R2_PUBLIC_BASE_URL:
        return VariantResult(original_url, False, "storage-unavailable")
    source_url = _prom_original_image_url(original_url)
    if not source_url:
        return VariantResult(original_url, False, "remote-url-rejected")
    try:
        response = requests.get(source_url, timeout=(5, 30))
        response.raise_for_status()
        content = response.content
        if not content or len(content) > 25 * 1024 * 1024:
            return VariantResult(original_url, False, "remote-size-invalid")
    except Exception as exc:
        logger.warning("Prom remote main image download failed: %s", exc)
        return VariantResult(original_url, False, "remote-download-failed")

    digest_hash = hashlib.sha256()
    digest_hash.update(f"{ALGORITHM_VERSION}:prom-remote".encode("ascii"))
    digest_hash.update(content)
    digest = digest_hash.hexdigest()[:24]
    derived_key = f"derived/prom-shafa-main/{ALGORITHM_VERSION}/remote-{digest}.webp"
    derived_url = r2_storage.public_url(derived_key)
    if not derived_url:
        return VariantResult(original_url, False, "public-url-unavailable")
    cache_root = Path(os.getenv(
        "PROM_IMAGE_VARIANT_CACHE_DIR",
        str(Path(product_images.get_images_dir()) / ".derived" / "prom-shafa-main"),
    )).expanduser()
    output = cache_root / ALGORITHM_VERSION / f"remote-{digest}.webp"
    uploaded = cache_root / ALGORITHM_VERSION / f"remote-{digest}.uploaded"
    skipped = cache_root / ALGORITHM_VERSION / f"remote-{digest}.skip"
    source = cache_root / ALGORITHM_VERSION / f"remote-{digest}.source"

    with _lock_for("remote-" + digest):
        if uploaded.exists():
            return VariantResult(derived_url, True, "cached")
        if skipped.exists():
            return VariantResult(
                original_url, False,
                skipped.read_text(encoding="utf-8") or "skipped",
            )
        try:
            if not output.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                temporary = source.with_suffix(".tmp")
                temporary.write_bytes(content)
                os.replace(temporary, source)
                try:
                    changed, reason = _render_safe_variant(source, output)
                finally:
                    source.unlink(missing_ok=True)
                if not changed:
                    _write_marker(skipped, reason)
                    return VariantResult(original_url, False, reason)
            r2_storage.upload_file(str(output), derived_key, content_type="image/webp")
            _write_marker(uploaded)
            return VariantResult(derived_url, True, "uploaded")
        except Exception as exc:
            logger.warning("Prom remote safe main image failed: %s", exc)
            return VariantResult(original_url, False, "processing-failed")
