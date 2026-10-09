#!/usr/bin/env python3
"""Ensure the shipped native LuCI catalogs contain the current translations."""
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MASK = 0xFFFFFFFF


def key_hash(text):
    # LuCI's canonical SuperFastHash. All source message IDs are ASCII.
    data = re.sub(r"\s+", " ", text).strip().encode("ascii")
    value = len(data)
    cursor = 0
    for _ in range(len(data) // 4):
        value = (value + int.from_bytes(data[cursor:cursor + 2], "little")) & MASK
        temp = ((int.from_bytes(data[cursor + 2:cursor + 4], "little") << 11) ^ value) & MASK
        value = ((value << 16) ^ temp) & MASK
        value = (value + (value >> 11)) & MASK
        cursor += 4
    remaining = len(data) % 4
    if remaining == 3:
        value = (value + int.from_bytes(data[cursor:cursor + 2], "little")) & MASK
        value = (value ^ (value << 16)) & MASK
        value = (value ^ (data[cursor + 2] << 18)) & MASK
        value = (value + (value >> 11)) & MASK
    elif remaining == 2:
        value = (value + int.from_bytes(data[cursor:cursor + 2], "little")) & MASK
        value = (value ^ (value << 11)) & MASK
        value = (value + (value >> 17)) & MASK
    elif remaining == 1:
        value = (value + data[cursor]) & MASK
        value = (value ^ (value << 10)) & MASK
        value = (value + (value >> 1)) & MASK
    for operation, shift in [("xor", 3), ("add", 5), ("xor", 4), ("add", 17), ("xor", 25), ("add", 6)]:
        value = ((value ^ (value << shift)) if operation == "xor" else (value + (value >> shift))) & MASK
    return value


def messages(path):
    result, key = {}, None
    for line in path.read_text().splitlines():
        if line.startswith("msgid "):
            key = json.loads(line[6:])
        elif line.startswith("msgstr ") and key:
            result[key] = json.loads(line[7:])
    return result


checked = 0
for language in json.loads((ROOT / "languages.json").read_text()):
    if language["po"] == "en":
        continue
    source = messages(ROOT / "po" / language["po"] / "wifi-client-limiter.po")
    binary = (ROOT / "files/usr/lib/lua/luci/i18n" / ("wifi-client-limiter." + language["runtime"] + ".lmo")).read_bytes()
    index = struct.unpack(">I", binary[-4:])[0]
    assert index < len(binary) - 4
    assert (len(binary) - 4 - index) % 16 == 0
    values = {}
    for cursor in range(index, len(binary) - 4, 16):
        key, _, offset, size = struct.unpack(">IIII", binary[cursor:cursor + 16])
        assert offset + size <= index
        values[key] = binary[offset:offset + size].decode("utf-8")
    for key, translated in source.items():
        actual = values.get(key_hash(key), key)
        assert actual == translated, (language["po"], key, actual, translated)
        checked += 1
print(f"PASS: {checked} compiled LuCI translation lookups match PO sources")
