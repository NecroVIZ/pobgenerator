"""Кодек импорт/экспорт-кодов Path of Building (транспортный слой).

Формат кода PoB (E19 в DESIGN-v2): ``base64url( zlib_deflate( build_xml ) )``.

Этот модуль покрывает ТОЛЬКО wire-формат (код <-> XML-байты). Он НЕ считает статы —
пересчёт DPS/EHP требует Lua-движка PoB (см. будущий модуль headless).

Важно (D24): коды PoB не идемпотентны при ре-кодировании — наш ``encode`` может выдать
валидный, но строково отличающийся от оригинала код (другой уровень компрессии / паддинг).
Поэтому round-trip проверяется по СТАТАМ/структуре XML, а не по равенству строк кода.
"""

from __future__ import annotations

import base64
import zlib


class PobCodeError(ValueError):
    """Невалидный или нераспознанный код PoB."""


def decode(code: str) -> bytes:
    """Декодировать код PoB в сырые XML-байты.

    Толерантен к отсутствию base64-паддинга и к пробелам/переводам строк.
    """
    cleaned = "".join(code.split())
    if not cleaned:
        raise PobCodeError("пустой код")
    # PoB использует URL-safe alphabet (- и _); паддинг может отсутствовать.
    padding = (-len(cleaned)) % 4
    cleaned_padded = cleaned + ("=" * padding)
    try:
        compressed = base64.urlsafe_b64decode(cleaned_padded)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise PobCodeError(f"невалидный base64: {exc}") from exc
    try:
        return zlib.decompress(compressed)
    except zlib.error as exc:
        # валидный base64, но не zlib-поток PoB (мусор/чужой формат/обрезка)
        raise PobCodeError(f"не удалось распаковать (повреждено или не код PoB): {exc}") from exc


def encode(xml: bytes) -> str:
    """Закодировать сырые XML-байты в код PoB (URL-safe base64 + zlib)."""
    if not isinstance(xml, (bytes, bytearray)):
        raise TypeError("encode ожидает bytes (XML), а не str")
    compressed = zlib.compress(bytes(xml), level=9)
    return base64.urlsafe_b64encode(compressed).decode("ascii")


def roundtrip_bytes(xml: bytes) -> bytes:
    """encode -> decode; должно вернуть исходные XML-байты без потерь."""
    return decode(encode(xml))
