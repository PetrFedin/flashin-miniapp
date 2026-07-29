import io

import pytest
from PIL import Image

from backend.services.media_pipeline import _safe_media_path
from backend.services.media_storage import _safe_filename, _sanitize_image


def _image_bytes(format_name: str, size=(32, 24), mode="RGB") -> bytes:
    image = Image.new(mode, size, (100, 120, 140) if mode == "RGB" else (100, 120, 140, 255))
    output = io.BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


def test_valid_png_is_reencoded_and_readable():
    raw = _image_bytes("PNG")

    sanitized, content_type, extension = _sanitize_image(raw, "image/png")

    assert content_type == "image/png"
    assert extension == ".png"
    with Image.open(io.BytesIO(sanitized)) as image:
        assert image.format == "PNG"
        assert image.size == (32, 24)


def test_declared_mime_must_match_actual_image():
    raw = _image_bytes("PNG")

    with pytest.raises(ValueError, match="does not match"):
        _sanitize_image(raw, "image/jpeg")


def test_invalid_image_content_is_rejected():
    with pytest.raises(ValueError, match="valid image"):
        _sanitize_image(b"not-an-image", "image/png")


def test_unsupported_content_type_is_rejected():
    raw = _image_bytes("PNG")

    with pytest.raises(ValueError, match="Only jpeg"):
        _sanitize_image(raw, "image/gif")


def test_windows_and_posix_upload_paths_are_reduced_to_basename():
    assert _safe_filename(r"C:\\fakepath\\photo.png", "fallback.png") == "photo.png"
    assert _safe_filename("../../photo.png", "fallback.png") == "photo.png"


def test_derivative_path_rejects_traversal(tmp_path):
    media_dir = tmp_path.resolve()

    with pytest.raises(ValueError):
        _safe_media_path(media_dir, "../outside.webp")

    assert _safe_media_path(media_dir, "safe.webp") == media_dir / "safe.webp"
