"""Тесты транспортного кодека PoB (чистый Python, без PoB-движка)."""

from __future__ import annotations

import pytest

from poebuildgen import pobcode


SAMPLE_XML = (
    b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
    b"<PathOfBuilding><Build level=\"90\" className=\"Templar\" "
    b"ascendClassName=\"Inquisitor\"><PlayerStat stat=\"TotalDPS\" value=\"1234567\"/>"
    b"</Build></PathOfBuilding>"
)


def test_encode_returns_url_safe_ascii():
    code = pobcode.encode(SAMPLE_XML)
    assert isinstance(code, str)
    # URL-safe base64 не содержит '+' или '/'.
    assert "+" not in code and "/" not in code


def test_byte_roundtrip_is_lossless():
    assert pobcode.roundtrip_bytes(SAMPLE_XML) == SAMPLE_XML


def test_decode_tolerates_missing_padding_and_whitespace():
    code = pobcode.encode(SAMPLE_XML)
    stripped = code.rstrip("=")
    spaced = "  " + "\n".join(stripped[i : i + 40] for i in range(0, len(stripped), 40)) + "\n"
    assert pobcode.decode(spaced) == SAMPLE_XML


def test_decode_rejects_empty():
    with pytest.raises(pobcode.PobCodeError):
        pobcode.decode("   ")


def test_decode_rejects_garbage():
    with pytest.raises(pobcode.PobCodeError):
        pobcode.decode("!!!!not-a-real-code!!!!")


def test_encode_rejects_str():
    with pytest.raises(TypeError):
        pobcode.encode("<xml/>")  # type: ignore[arg-type]
