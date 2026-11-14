#!/usr/bin/env python3
"""Nanocore NAM A2 Lite CLI.

Reads and writes the Nanocore's AMP slots over USB MIDI SysEx, and converts
canonical two-submodel A2 .nam files to the pedal's 7516-byte NA2L blob.

The protocol and NA2L layout were verified against a real Nanocore running
firmware 1.2 and ToneCommand 1.3.5:

  NA2L header (little endian, 32 bytes):
    4s  magic "NA2L"
    H   version 1
    H   header size 32
    I   sample rate 48000
    I   weight count 1871
    I   payload bytes 7484
    I   zlib CRC-32 of the payload
    I   1
    I   0
  payload: 1871 little-endian float32 A2 Lite weights

Commands used (same framing as ToneCommand):
  0x30 storage info, 0x36 slot info, 0x39 read
  0x31 begin, 0x32 chunk, 0x33 commit, 0x35 name
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import mido
except ImportError:  # pragma: no cover
    mido = None

SYSEX_PREFIX = bytes.fromhex("f0 7d 4e 43")
HOST_TO_DEVICE = 0x70
DEVICE_TO_HOST = 0x71
SYSEX_REPLY_PREFIX = bytes.fromhex("7d 4e 43 71")

NA2L_MAGIC = b"NA2L"
NA2L_VERSION = 1
NA2L_HEADER_SIZE = 32
NA2L_SAMPLE_RATE = 48000
NA2L_WEIGHT_COUNT = 1871
NA2L_PAYLOAD_SIZE = NA2L_WEIGHT_COUNT * 4
NA2L_TOTAL_SIZE = NA2L_HEADER_SIZE + NA2L_PAYLOAD_SIZE
NA2L_HEADER_TAIL = (1, 0)

AMP_INFO = 0x30
AMP_BEGIN = 0x31
AMP_CHUNK = 0x32
AMP_COMMIT = 0x33
AMP_ABORT = 0x34
AMP_NAME = 0x35
AMP_SLOT_INFO = 0x36
AMP_READ = 0x39
AMP_SLOT_COUNT = 30
AMP_TOTAL_SLOTS = 38
AMP_CHUNK_SIZE = 64
AMP_LIMIT = 12288


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def pack_7bit(data: bytes) -> bytes:
    out = bytearray()
    for offset in range(0, len(data), 7):
        group = data[offset : offset + 7]
        mask = sum(((byte >> 7) & 1) << index for index, byte in enumerate(group))
        out.append(mask)
        out.extend(byte & 0x7F for byte in group)
    return bytes(out)


def unpack_7bit(data: bytes) -> bytes:
    out = bytearray()
    offset = 0
    while offset < len(data):
        mask = data[offset]
        offset += 1
        group = data[offset : offset + 7]
        if not group:
            raise ValueError("truncated 7-bit group")
        out.extend(byte | (((mask >> index) & 1) << 7) for index, byte in enumerate(group))
        offset += len(group)
    return bytes(out)


def encode_message(command: int, payload: bytes = b"", sequence: int = 1) -> "mido.Message":
    if mido is None:
        raise RuntimeError("mido is not installed")
    raw = struct.pack("<BHHH", 2, sequence & 0xFFFF, command, len(payload)) + payload
    frame = SYSEX_PREFIX + bytes([HOST_TO_DEVICE]) + pack_7bit(raw) + b"\xF7"
    return mido.Message("sysex", data=list(frame[1:-1]))


@dataclass(frozen=True)
class SlotInfo:
    slot: int
    present: bool
    active: bool
    size: int
    checksum: int
    format_id: int
    name: str
    asset_type: int
    raw: bytes


@dataclass(frozen=True)
class StorageInfo:
    slot_count: int
    max_bytes: int
    active_slot: int | None
    raw: bytes


def parse_storage_info(payload: bytes) -> StorageInfo:
    if len(payload) < 20:
        raise ValueError("storage info is shorter than 20 bytes")
    count, limit = struct.unpack_from("<II", payload, 8)
    active = int.from_bytes(payload[16:18], "little")
    return StorageInfo(count, limit, None if active == 0xFFFF else active, payload)


def parse_slot_info(payload: bytes) -> SlotInfo:
    if len(payload) < 30:
        raise ValueError("slot info is shorter than 30 bytes")
    slot, present, active = payload[:3]
    size = int.from_bytes(payload[3:7], "little")
    checksum = int.from_bytes(payload[7:11], "little")
    format_id = int.from_bytes(payload[11:13], "little")
    name = payload[14:30].decode("ascii", errors="replace").rstrip(" \0")
    asset_type = payload[30] if len(payload) > 30 else 0
    return SlotInfo(slot, bool(present), bool(active), size, checksum, format_id, name, asset_type, payload)


def build_na2l(nam_path: str | Path) -> bytes:
    """Convert a canonical two-submodel A2 .nam to the pedal's NA2L blob."""
    with open(nam_path, "r", encoding="utf-8") as handle:
        model = json.load(handle)
    submodels = model.get("config", {}).get("submodels")
    if not isinstance(submodels, list) or len(submodels) < 2:
        raise ValueError(f"{nam_path}: not a two-submodel SlimmableContainer")
    lite = submodels[0]
    if lite.get("max_value") != 0.5:
        raise ValueError(f"{nam_path}: first submodel is not max_value 0.5")
    weights = lite.get("model", {}).get("weights")
    if not isinstance(weights, list) or len(weights) != NA2L_WEIGHT_COUNT:
        raise ValueError(
            f"{nam_path}: first submodel has {len(weights) if isinstance(weights, list) else 'no'} weights, "
            f"expected {NA2L_WEIGHT_COUNT}"
        )
    payload = struct.pack(f"<{len(weights)}f", *weights)
    header = struct.pack(
        "<4sHHIIIIII",
        NA2L_MAGIC,
        NA2L_VERSION,
        NA2L_HEADER_SIZE,
        NA2L_SAMPLE_RATE,
        len(weights),
        len(payload),
        crc32(payload),
        NA2L_HEADER_TAIL[0],
        NA2L_HEADER_TAIL[1],
    )
    return header + payload


def validate_na2l(data: bytes) -> tuple[int, int]:
    if len(data) != NA2L_TOTAL_SIZE:
        raise ValueError(f"NA2L is {len(data)} bytes, expected {NA2L_TOTAL_SIZE}")
    magic, version, header_size, rate, count, payload_size, checksum, tail0, tail1 = struct.unpack(
        "<4sHHIIIIII", data[:NA2L_HEADER_SIZE]
    )
    if magic != NA2L_MAGIC:
        raise ValueError("NA2L magic is invalid")
    if version != NA2L_VERSION:
        raise ValueError(f"NA2L version is {version}, expected {NA2L_VERSION}")
    if header_size != NA2L_HEADER_SIZE:
        raise ValueError(f"NA2L header size is {header_size}, expected {NA2L_HEADER_SIZE}")
    if rate != NA2L_SAMPLE_RATE:
        raise ValueError(f"NA2L sample rate is {rate}, expected {NA2L_SAMPLE_RATE}")
    if count != NA2L_WEIGHT_COUNT or payload_size != NA2L_PAYLOAD_SIZE:
        raise ValueError(f"NA2L shape is {count} weights / {payload_size} bytes, expected 1871 / 7484")
    actual = crc32(data[NA2L_HEADER_SIZE:])
    if actual != checksum:
        raise ValueError(f"NA2L CRC is {actual:08x}, header says {checksum:08x}")
    if (tail0, tail1) != NA2L_HEADER_TAIL:
        raise ValueError(f"NA2L header tail is {tail0}, {tail1}, expected {NA2L_HEADER_TAIL}")
    return count, checksum


class Nano:
    def __init__(self, port: str | None = None, verbose: bool = True):
        if mido is None:
            raise RuntimeError("install mido and python-rtmidi first")
        self.verbose = verbose
        self.inp = mido.open_input(port or self._find_port())
        self.out = mido.open_output(port or self._find_port())
        self.sequence = 0
        self._last_status = 0

    @staticmethod
    def _find_port() -> str:
        names = mido.get_output_names()
        for name in names:
            if "nanocore" in name.lower():
                return name
        if len(names) == 1:
            return names[0]
        raise RuntimeError(f"could not choose a Nanocore MIDI port from {names}")

    def _log(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)

    def request(self, command: int, payload: bytes = b"", timeout: float = 2.0) -> tuple[int, bytes]:
        self.sequence = (self.sequence + 1) & 0xFFFF
        sequence = self.sequence
        self.out.send(encode_message(command, payload, sequence))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self.inp.poll()
            if message is None:
                time.sleep(0.001)
                continue
            if message.type != "sysex":
                continue
            data = bytes(message.data)
            if not data.startswith(SYSEX_REPLY_PREFIX):
                continue
            try:
                raw = unpack_7bit(data[4:])
            except ValueError:
                continue
            if len(raw) < 8 or raw[0] != 2:
                continue
            got_sequence, got_command = struct.unpack_from("<HH", raw, 1)
            if got_sequence != sequence or got_command != command:
                continue
            status = raw[5]
            length = int.from_bytes(raw[6:8], "little")
            return status, raw[8 : 8 + length]
        raise TimeoutError(f"no reply for command 0x{command:02x}")

    def info(self) -> StorageInfo:
        status, payload = self.request(AMP_INFO, b"", 3.0)
        if status != 0:
            raise RuntimeError(f"storage info failed with status {status}")
        return parse_storage_info(payload)

    def slot_info(self, slot: int) -> SlotInfo:
        status, payload = self.request(AMP_SLOT_INFO, bytes([slot]), 3.0)
        if status != 0:
            raise RuntimeError(f"slot {slot} info failed with status {status}")
        return parse_slot_info(payload)

    def read_slot(self, slot: int, expected_size: int | None = None) -> bytes:
        info = self.slot_info(slot)
        if not info.present or info.size == 0:
            return b""
        size = expected_size or info.size
        if size != info.size:
            raise ValueError(f"slot {slot} is {info.size} bytes, not {size}")
        data = bytearray()
        offset = 0
        while offset < size:
            length = min(200, size - offset)
            status, reply = self.request(AMP_READ, struct.pack("<BHB", slot, offset, length), 2.0)
            if status != 0:
                raise RuntimeError(f"read slot {slot} at {offset} failed with status {status}")
            if len(reply) < 5:
                raise RuntimeError(f"short read reply for slot {slot} at {offset}")
            got_slot, got_offset, total = struct.unpack_from("<BHH", reply)
            chunk = reply[5:]
            if got_slot != slot or got_offset != offset or total != size:
                raise RuntimeError(
                    f"read reply mismatch for slot {slot}: got {got_slot}/{got_offset}/{total}, "
                    f"expected {slot}/{offset}/{size}"
                )
            data.extend(chunk)
            if not chunk:
                break
            offset += len(chunk)
        if len(data) != size:
            raise RuntimeError(f"read {len(data)} of {size} bytes from slot {slot}")
        actual = crc32(data)
        if actual != info.checksum:
            raise RuntimeError(f"slot {slot} CRC mismatch: read {actual:08x}, slot says {info.checksum:08x}")
        self._log(f"slot {slot:02d}: read {len(data)} bytes, {info.name!r}, crc {actual:08x}")
        return bytes(data)

    def write_slot(self, slot: int, data: bytes, name: str | None = None, chunk_size: int = AMP_CHUNK_SIZE) -> SlotInfo:
        if not 0 <= slot < AMP_SLOT_COUNT:
            raise ValueError(f"slot {slot} is outside the 30 AMP slots")
        validate_na2l(data)
        storage = self.info()
        if slot >= storage.slot_count:
            raise ValueError(f"pedal reports {storage.slot_count} slots, cannot write {slot}")
        if len(data) > storage.max_bytes:
            raise ValueError(f"{len(data)} bytes is above the pedal limit of {storage.max_bytes}")
        checksum = crc32(data)
        self._log(f"slot {slot:02d}: writing {len(data)} bytes in {chunk_size}-byte chunks, crc {checksum:08x}")
        status, _ = self.request(AMP_BEGIN, struct.pack("<I", len(data) | (slot << 24)), 10.0)
        if status != 0:
            raise RuntimeError(f"begin failed with status {status}")
        try:
            for offset in range(0, len(data), chunk_size):
                piece = data[offset : offset + chunk_size]
                status, _ = self.request(
                    AMP_CHUNK, struct.pack("<I", offset | (slot << 24)) + piece, 2.0
                )
                if status != 0:
                    raise RuntimeError(f"chunk at {offset} failed with status {status}")
                if self.verbose and offset and offset % (chunk_size * 16) == 0:
                    self._log(f"slot {slot:02d}: {offset}/{len(data)}")
            status, _ = self.request(
                AMP_COMMIT,
                struct.pack("<I", len(data) | (slot << 24)) + struct.pack("<I", checksum),
                12.0,
            )
            if status != 0:
                raise RuntimeError(f"commit failed with status {status}")
        except Exception:
            try:
                self.request(AMP_ABORT, b"", 0.5)
            except Exception:
                pass
            raise
        if name is not None:
            encoded = encode_name(name)
            status, _ = self.request(AMP_NAME, bytes([slot]) + encoded, 2.0)
            if status != 0:
                raise RuntimeError(f"set name failed with status {status}")
        info = self.slot_info(slot)
        if info.size != len(data) or info.checksum != checksum:
            raise RuntimeError(
                f"slot {slot} verify failed: size {info.size}/{len(data)}, "
                f"crc {info.checksum:08x}/{checksum:08x}"
            )
        self._log(f"slot {slot:02d}: committed and verified, name {info.name!r}")
        return info


def encode_name(name: str, maximum: int = 16) -> bytes:
    cleaned = "".join(char if 0x20 <= ord(char) <= 0x7E else " " for char in name).strip()
    cleaned = cleaned[:maximum].rstrip()
    if not cleaned:
        raise ValueError("slot name is empty")
    return cleaned.encode("ascii")


def resolve_ports(port: str | None) -> str | None:
    return port


def cmd_info(args: argparse.Namespace) -> int:
    nano = Nano(args.port)
    storage = nano.info()
    print(f"slots={storage.slot_count} max_bytes={storage.max_bytes} active={storage.active_slot}")
    for slot in range(min(storage.slot_count, AMP_TOTAL_SLOTS)):
        info = nano.slot_info(slot)
        kind = {1: "DDPB", 3: "NA2L"}.get(info.asset_type, f"type{info.asset_type}")
        print(
            f"{slot:02d} present={int(info.present)} active={int(info.active)} "
            f"size={info.size:5d} crc={info.checksum:08x} {kind:4s} {info.name!r}"
        )
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    nano = Nano(args.port)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for slot in range(args.start, args.start + args.count):
        info = nano.slot_info(slot)
        if not info.present:
            print(f"slot {slot:02d}: empty")
            continue
        data = nano.read_slot(slot)
        path = output / f"amp_{slot:02d}.bin"
        path.write_bytes(data)
        manifest.append(
            {
                "slot": slot,
                "name": info.name,
                "asset_type": info.asset_type,
                "size": len(data),
                "checksum": crc32(data),
                "file": path.name,
            }
        )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {len(manifest)} slots to {output}")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    source = Path(args.nam)
    data = build_na2l(source)
    validate_na2l(data)
    target = Path(args.output) if args.output else source.with_suffix(".na2l")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"{source.name} -> {target} ({len(data)} bytes, crc {crc32(data[32:]):08x})")
    return 0


def cmd_convert_dir(args: argparse.Namespace) -> int:
    source = Path(args.directory)
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(source.glob("*.nam")):
        data = build_na2l(path)
        validate_na2l(data)
        (target / path.with_suffix(".na2l").name).write_bytes(data)
        count += 1
        print(f"{path.name} -> {path.with_suffix('.na2l').name}")
    print(f"converted {count} files to {target}")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    nano = Nano(args.port)
    data = Path(args.file).read_bytes()
    nano.write_slot(args.slot, data, args.name)
    return 0


def cmd_upload_dir(args: argparse.Namespace) -> int:
    nano = Nano(args.port)
    directory = Path(args.directory)
    files = sorted(directory.glob("*.na2l"))
    if not files:
        raise SystemExit(f"no .na2l files in {directory}")
    names = {}
    if args.names:
        names = json.loads(Path(args.names).read_text(encoding="utf-8"))
    start = args.start
    if len(files) > AMP_SLOT_COUNT - start:
        raise SystemExit(f"{len(files)} files do not fit from slot {start}")
    for index, path in enumerate(files):
        slot = start + index
        name = names.get(str(slot + 1)) or names.get(path.stem) or path.stem
        print(f"\n[{index + 1}/{len(files)}] slot {slot:02d}: {path.name}")
        nano.write_slot(slot, path.read_bytes(), name)
    print(f"\nwrote {len(files)} slots")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    nano = Nano(args.port)
    directory = Path(args.directory)
    files = sorted(directory.glob("*.na2l"))
    if not files:
        raise SystemExit(f"no .na2l files in {directory}")
    start = args.start
    failures = 0
    for index, path in enumerate(files):
        slot = start + index
        data = path.read_bytes()
        validate_na2l(data)
        info = nano.slot_info(slot)
        ok = info.size == len(data) and info.checksum == crc32(data)
        print(f"slot {slot:02d}: {'OK' if ok else 'FAIL'} size={info.size} crc={info.checksum:08x} name={info.name!r}")
        failures += 0 if ok else 1
    return 1 if failures else 0


FX_OS_SIZE = 868352
FX_REPO_URL = "https://github.com/jcuhnoio/re-nanocore.git"
FX_BACKUP_MARKER = "BACKUP.json"
# descriptor, firmwareVersion, effect, replaces, stock hash, installed hash (static fallback)
FX_FALLBACK_CATALOG = (
    ("web/patch.json", "1.10", "UNIVIBE", "VELVET VIBRATO", "ed84c017176e", "8c6d6b27a0df"),
    ("web/patch-1.11.json", "1.11", "UNIVIBE", "VELVET VIBRATO", "688aae0ddba6", "4f979ab97970"),
    ("web/patch-1.12.json", "1.12", "UNIVIBE", "VELVET VIBRATO", "17ce4dc1fb0e", "cf31fb0baaa3"),
    ("web/octave-1.12.json", "1.12", "OCTAVE", "FX2/PITCH subtype 8 (Octaver)", "96b005077792",
     "3af8c8d2abea"),
)
FX_DEPLOY_WARNING = (
    "WARNING: this raw DFU deploy path is unvalidated on our hardware. "
    "ToneCommand must be closed, headphones/audio unplugged, and power must not be lost."
)


def _resolve_repo(explicit: str | None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(Path.home() / "nanocore_tools" / "repos" / "re-nanocore")
    candidates.append(Path.cwd() / "re-nanocore")
    for candidate in candidates:
        if (candidate / "web" / "patch-1.12.json").is_file():
            return candidate
    tried = ", ".join(str(path) for path in candidates)
    raise RuntimeError(
        f"no re-nanocore checkout containing web/patch-1.12.json (tried {tried}); "
        f"clone {FX_REPO_URL}"
    )


def _snake_value(value):
    """Convert the patch JSON's JS camelCase keys to Python snake_case, recursively."""
    if isinstance(value, dict):
        return {_snake_key(key): _snake_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_snake_value(item) for item in value]
    return value


def _snake_key(key: str) -> str:
    return "".join("_" + char.lower() if char.isupper() else char for char in key)


def _load_patch(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"patch descriptor {path} does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path}: patch descriptor is not a JSON object")
    return _snake_value(raw)


def _hex_bytes(entry: dict, *names: str) -> bytes:
    for name in names:
        if name not in entry or entry[name] is None:
            continue
        text = entry[name]
        if not isinstance(text, str) or not text or len(text) % 2:
            raise RuntimeError(f"patch field {name!r} is not an even-length hex string")
        try:
            return bytes.fromhex(text)
        except ValueError as exc:
            raise RuntimeError(f"patch field {name!r} is not valid hex") from exc
    raise RuntimeError(f"patch entry has none of the hex fields {', '.join(names)}")


def _installed_digest(output: bytes, fit_size: int, expected: str) -> str:
    prefix = hashlib.sha256(output[:fit_size]).hexdigest()
    if prefix == expected:
        return prefix
    whole = hashlib.sha256(output).hexdigest()
    if whole == expected:
        return whole
    raise RuntimeError(
        f"patched image hash {prefix} (first {fit_size} bytes) or {whole} (whole file) does not "
        f"match installedFitSha256 {expected}; nothing will be written."
    )


def _check_segments(segments: list, fit_size: int) -> None:
    seen: list[tuple[int, int]] = []
    for segment in segments:
        offset = int(segment.get("offset", -1))
        size = int(segment.get("size", 0))
        if offset < 0 or size <= 0 or offset + size > fit_size:
            raise RuntimeError(f"segment bounds {offset}+{size} are outside the {fit_size}-byte FIT")
        for start, length in seen:
            if offset < start + length and start < offset + size:
                raise RuntimeError(f"segment {offset}+{size} overlaps {start}+{length}")
        seen.append((offset, size))


def _prepare_v1(image: bytes, manifest: dict) -> tuple[bytes, dict]:
    os_size = int(manifest.get("os_size", 0))
    fit_size = int(manifest.get("fit_size", 0))
    if not 0 < fit_size <= os_size:
        raise RuntimeError("unsupported patch manifest: invalid fitSize/osSize")
    if len(image) != os_size:
        raise RuntimeError(f"expected a complete {os_size}-byte OS partition, got {len(image)} bytes")
    ranges: list[tuple[int, int]] = []

    def reserve(offset: int, size: int, label: str) -> None:
        if offset < 0 or size <= 0 or offset + size > fit_size:
            raise RuntimeError(f"{label} range {offset}+{size} is outside the {fit_size}-byte FIT")
        for start, length in ranges:
            if offset < start + length and start < offset + size:
                raise RuntimeError(f"{label} range {offset}+{size} overlaps {start}+{length}")
        ranges.append((offset, size))

    timestamp = manifest.get("timestamp") or {}
    if int(timestamp.get("size", 0)) != 4:
        raise RuntimeError("unsupported patch manifest: timestamp must be 4 bytes")
    reserve(int(timestamp["offset"]), 4, "timestamp")
    entries = list(manifest.get("patches") or []) + list(manifest.get("hashes") or [])
    prepared: list[tuple[int, bytes, bytes]] = []
    for entry in entries:
        before = _hex_bytes(entry, "before_hex", "before")
        after = _hex_bytes(entry, "after_hex", "after")
        if len(before) != len(after):
            raise RuntimeError("patch changes image size")
        offset = int(entry.get("offset", -1))
        reserve(offset, len(before), "patch")
        prepared.append((offset, before, after))
    segments = list(manifest.get("segments") or [])
    _check_segments(segments, fit_size)
    normalized = bytearray(image[:fit_size])
    for offset, size in ranges:
        normalized[offset : offset + size] = bytes(size)
    layout = hashlib.sha256(normalized).hexdigest()
    if layout != manifest.get("normalized_fit_sha256"):
        raise RuntimeError("Unknown firmware layout or build; nothing will be written.")
    for offset, before, _ in prepared:
        if image[offset : offset + len(before)] != before:
            raise RuntimeError(f"firmware bytes at {offset} do not match the patch's beforeHex")
    for segment in segments:
        offset = int(segment["offset"])
        size = int(segment["size"])
        actual = hashlib.sha256(image[offset : offset + size]).hexdigest()
        if actual != segment.get("stock_sha256"):
            raise RuntimeError(f"segment {segment.get('name', offset)} sha256 is {actual}, not stock")
    output = bytearray(image)
    for offset, _, after in prepared:
        output[offset : offset + len(after)] = after
    append = manifest.get("append")
    if append:
        payload = _hex_bytes(append, "after_hex", "after", "hex")
        offset = int(append.get("offset", -1))
        output[offset : offset + len(payload)] = payload
    digest = _installed_digest(bytes(output), fit_size, str(manifest.get("installed_fit_sha256", "")))
    summary = {"firmware": manifest.get("firmware_version"), "effect": manifest.get("effect"),
               "patches": len(prepared), "hash": digest}
    return bytes(output), summary


def _prepare_v2(image: bytes, manifest: dict) -> tuple[bytes, dict]:
    os_size = int(manifest.get("os_size", 0))
    stock_size = int(manifest.get("stock_fit_size", 0))
    installed_size = int(manifest.get("installed_fit_size", 0))
    protected = int(manifest.get("protected_offset") or installed_size)
    if not 0 < stock_size < installed_size <= protected <= os_size:
        raise RuntimeError("unsupported extended patch manifest: invalid FIT/protected bounds")
    if len(image) != os_size:
        raise RuntimeError(f"expected a complete {os_size}-byte OS partition, got {len(image)} bytes")
    timestamp = manifest.get("timestamp") or {}
    if int(timestamp.get("size", 0)) != 4:
        raise RuntimeError("unsupported extended patch manifest: timestamp must be 4 bytes")
    ranges = [(int(timestamp["offset"]), 4)]
    if ranges[0][0] < 0 or ranges[0][0] + 4 > stock_size:
        raise RuntimeError("unsupported extended patch manifest: timestamp outside the stock FIT")
    prepared: list[tuple[int, bytes, bytes]] = []
    for entry in manifest.get("patches") or []:
        before = _hex_bytes(entry, "before_hex", "before")
        after = _hex_bytes(entry, "after_hex", "after")
        if len(before) != len(after):
            raise RuntimeError("patch changes image size")
        offset = int(entry.get("offset", -1))
        if offset < 0 or offset + len(before) > stock_size:
            raise RuntimeError(f"patch range {offset}+{len(before)} is outside the stock FIT")
        for start, length in ranges:
            if offset < start + length and start < offset + len(before):
                raise RuntimeError(f"patch range {offset}+{len(before)} overlaps {start}+{length}")
        ranges.append((offset, len(before)))
        prepared.append((offset, before, after))
    append = manifest.get("append") or {}
    payload = _hex_bytes(append, "after_hex", "after", "hex")
    if int(append.get("offset", -1)) != stock_size:
        raise RuntimeError("unsupported extended patch manifest: append offset is not stockFitSize")
    if len(payload) != installed_size - stock_size:
        raise RuntimeError("invalid appended payload length")
    normalized = bytearray(image[:stock_size])
    ts_offset = int(timestamp["offset"])
    normalized[ts_offset : ts_offset + 4] = bytes(4)
    digest = hashlib.sha256(normalized).hexdigest()
    if digest != manifest.get("stock_normalized_sha256"):
        raise RuntimeError("Unknown or damaged extended firmware; nothing will be written.")
    for offset, before, _ in prepared:
        if image[offset : offset + len(before)] != before:
            raise RuntimeError(f"firmware bytes at {offset} do not match the patch's beforeHex")
    output = bytearray(image)
    for offset, _, after in prepared:
        output[offset : offset + len(after)] = after
    output[stock_size : stock_size + len(payload)] = payload
    installed = bytearray(output[:installed_size])
    installed[int(timestamp["offset"]) : int(timestamp["offset"]) + 4] = bytes(4)
    installed_hash = hashlib.sha256(installed).hexdigest()
    if installed_hash != manifest.get("installed_normalized_sha256"):
        raise RuntimeError(
            f"patched image hash {installed_hash} does not match installedNormalizedSha256; "
            "nothing will be written."
        )
    summary = {"firmware": manifest.get("firmware_version"), "effect": manifest.get("effect"),
               "patches": len(prepared), "hash": installed_hash}
    return bytes(output), summary


def _as_os_partition(image: bytes, manifest: dict) -> bytes:
    os_size = int(manifest.get("os_size", 0))
    if len(image) == os_size:
        return image
    fit_size = int(manifest.get("stock_fit_size") or manifest.get("fit_size") or 0)
    if 0 < fit_size <= len(image) < os_size:
        print(f"note: padding the decoded {len(image)}-byte firmware payload to the {os_size}-byte "
              "OS partition; the tail beyond the FIT is device-owned", flush=True)
        return image + bytes(os_size - len(image))
    raise RuntimeError(f"expected a complete {os_size}-byte OS partition, got {len(image)} bytes")


def _prepare_image(image: bytes, manifest: dict) -> tuple[bytes, dict]:
    schema = manifest.get("schema_version")
    if schema not in (1, 2):
        raise RuntimeError(f"unsupported patch manifest schemaVersion {schema!r}")
    image = _as_os_partition(image, manifest)
    if schema == 1:
        return _prepare_v1(image, manifest)
    return _prepare_v2(image, manifest)


def _decode_fmu(fmu: Path, repo: Path) -> bytes:
    tool = repo / "tools" / "firmware_decode" / "decode.py"
    if not tool.is_file():
        raise RuntimeError(f"{tool} is missing; the checkout at {repo} is incomplete")
    if not fmu.is_file():
        raise RuntimeError(f"firmware package {fmu} does not exist")
    with tempfile.TemporaryDirectory(prefix="nanocore-fmu-") as tmp:
        target = Path(tmp) / "decoded"
        result = subprocess.run(
            [sys.executable, str(tool), str(fmu), "--output", str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or "no output"
            hint = " (pip install cryptography)" if "cryptography" in detail else ""
            raise RuntimeError(
                f"firmware_decode exited {result.returncode}: {detail}{hint}"
            )
        blob = target / "firmware.bin"
        if not blob.is_file():
            raise RuntimeError("firmware_decode produced no firmware.bin")
        return blob.read_bytes()


def cmd_effects_list(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    print(f"{'firmware':8s}  {'effect':14s}  {'replaces':34s}  {'stock':12s}  {'installed':12s}")
    for descriptor, version, effect, replaces, stock, installed in FX_FALLBACK_CATALOG:
        if (repo / descriptor).is_file():
            manifest = _load_patch(repo / descriptor)
            effect = str(manifest.get("effect", effect))
            replaced = manifest.get("replaces", replaces)
            if isinstance(replaced, list):
                replaced = " + ".join(str(item) for item in replaced)
            stock = str(manifest.get("stock_fit_sha256") or manifest.get("stock_normalized_sha256")
                        or stock)
            installed = str(manifest.get("installed_fit_sha256")
                            or manifest.get("installed_normalized_sha256") or installed)
            version = str(manifest.get("firmware_version", version))
        else:
            replaced = replaces
        print(f"{version:8s}  {effect:14s}  {str(replaced):34s}  {stock[:12]}  {installed[:12]}")
    return 0


def cmd_effects_prepare(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo)
    manifest = _load_patch(Path(args.patch))
    image = _decode_fmu(Path(args.fmu), repo)
    output, summary = _prepare_image(image, manifest)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(output)
    print(f"firmware {summary['firmware'] or '?'} effect {summary['effect'] or '?'}, "
          f"{summary['patches']} patches, {len(output)} bytes")
    print(f"wrote {target}")
    print(f"installed hash {summary['hash']}")
    print("image is ready; nothing was sent to the pedal.")
    return 0


def _backup_digest(directory: Path, record: dict) -> str:
    names = record.get("files")
    if not isinstance(names, list) or not names:
        raise RuntimeError(f"{directory / FX_BACKUP_MARKER} has no ordered 'files' list")
    lines = []
    for name in names:
        path = directory / str(name)
        if not path.is_file():
            raise RuntimeError(f"backup file {path} is missing")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()} {path.name}")
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def cmd_effects_backup(args: argparse.Namespace) -> int:
    nano = Nano(args.port)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    files = []
    for slot in range(AMP_TOTAL_SLOTS):
        info = nano.slot_info(slot)
        if not info.present:
            print(f"slot {slot:02d}: empty")
            continue
        data = nano.read_slot(slot)
        path = output / f"amp_{slot:02d}.bin"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        files.append(path.name)
        manifest.append({"slot": slot, "name": info.name, "asset_type": info.asset_type,
                         "size": len(data), "crc32": crc32(data), "sha256": digest,
                         "file": path.name})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    lines = [f"{entry['sha256']} {entry['file']}" for entry in manifest]
    combined = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    record = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "slot_count": len(manifest), "port": args.port or "", "sha256": combined,
              "files": files}
    (output / FX_BACKUP_MARKER).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest)} slots to {output}")
    print(f"backup hash {combined}")
    print("keep this hash: effects deploy requires it as the verified --backup-dir identity.")
    return 0


def _find_os_image(directory: Path) -> Path | None:
    for name in ("os-partition.bin", "firmware.bin", "stock-os.bin"):
        candidate = directory / name
        if candidate.is_file() and candidate.stat().st_size == FX_OS_SIZE:
            return candidate
    for candidate in sorted(directory.glob("*.bin")):
        if candidate.stat().st_size == FX_OS_SIZE:
            return candidate
    return None


def cmd_effects_deploy(args: argparse.Namespace) -> int:
    if not args.yes:
        print("refusing to write the pedal without --yes. Physical preconditions:", file=sys.stderr)
        print("  1. ToneCommand must be closed.", file=sys.stderr)
        print("  2. Headphones/audio must be physically unplugged.", file=sys.stderr)
        print("  3. The pedal must not lose power.", file=sys.stderr)
        return 2
    repo = _resolve_repo(args.repo)
    backup_dir = Path(args.backup_dir)
    if not backup_dir.is_dir():
        raise RuntimeError(f"backup directory {backup_dir} does not exist")
    record_path = backup_dir / FX_BACKUP_MARKER
    if not record_path.is_file():
        raise RuntimeError(f"{record_path} is missing; run 'effects backup' first")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{record_path}: invalid JSON ({exc})") from exc
    actual = _backup_digest(backup_dir, record)
    if actual != record.get("sha256"):
        raise RuntimeError(f"backup hash {actual} does not match {record_path}; refusing")
    source = _find_os_image(backup_dir)
    if source is None:
        raise RuntimeError(
            f"raw_dfu.py deploy needs the full {FX_OS_SIZE}-byte stock OS partition in "
            f"{backup_dir} (os-partition.bin); the AMP slot backup alone cannot satisfy --backup"
        )
    image = Path(args.image)
    if not image.is_file():
        raise RuntimeError(f"image {image} does not exist")
    image_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    if image_hash != str(args.expect_sha256):
        print(f"note: {image.name} sha256 is {image_hash}, not the expected {args.expect_sha256}; "
              "continuing because installed hashes may cover only the FIT prefix", flush=True)
    manifest = Path(str(image) + ".manifest.json")
    if not manifest.is_file():
        raise RuntimeError(f"{manifest} is missing; raw_dfu.py deploy requires a patch plan")
    if not args.current_sha256:
        raise RuntimeError("raw_dfu.py deploy requires --current-sha256: the live OS hash read "
                           "from the pedal before any write")
    tool = repo / "tools" / "custom_fx" / "raw_dfu.py"
    if not tool.is_file():
        raise RuntimeError(f"{tool} is missing; the checkout at {repo} is incomplete")
    receipt = Path(tempfile.gettempdir()) / time.strftime("nanocore-deploy-%Y%m%d-%H%M%S")
    command = [sys.executable, str(tool), "deploy", "--output", str(receipt),
               "--backup", str(source), "--backup-sha256", str(record["sha256"]),
               "--expected-current-sha256", str(args.current_sha256), "--source", str(source),
               "--image", str(image), "--manifest", str(manifest)]
    print(FX_DEPLOY_WARNING, flush=True)
    print(f"receipt directory: {receipt}", flush=True)
    print("running: " + " ".join(command), flush=True)
    result = subprocess.run(command)
    if result.returncode != 0:
        raise RuntimeError(f"raw_dfu.py deploy exited {result.returncode}; inspect {receipt}")
    return 0


def _run_effects(func, args: argparse.Namespace) -> int:
    try:
        return func(args)
    except (RuntimeError, ValueError, OSError, KeyError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nanocore NAM A2 Lite CLI")
    parser.add_argument("--port", help="MIDI port name (default: first port containing Nanocore)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="show storage info and all AMP slots")
    p_info.set_defaults(func=cmd_info)

    p_dump = sub.add_parser("dump", help="back up AMP slots to .bin files")
    p_dump.add_argument("output")
    p_dump.add_argument("--start", type=int, default=0)
    p_dump.add_argument("--count", type=int, default=AMP_TOTAL_SLOTS)
    p_dump.set_defaults(func=cmd_dump)

    p_convert = sub.add_parser("convert", help="convert one canonical .nam to .na2l")
    p_convert.add_argument("nam")
    p_convert.add_argument("-o", "--output")
    p_convert.set_defaults(func=cmd_convert)

    p_convert_dir = sub.add_parser("convert-dir", help="convert every .nam in a folder")
    p_convert_dir.add_argument("directory")
    p_convert_dir.add_argument("-o", "--output", required=True)
    p_convert_dir.set_defaults(func=cmd_convert_dir)

    p_upload = sub.add_parser("upload", help="write one .na2l to one AMP slot")
    p_upload.add_argument("slot", type=int)
    p_upload.add_argument("file")
    p_upload.add_argument("--name")
    p_upload.set_defaults(func=cmd_upload)

    p_upload_dir = sub.add_parser("upload-dir", help="write a folder of .na2l files to consecutive slots")
    p_upload_dir.add_argument("directory")
    p_upload_dir.add_argument("--start", type=int, default=0)
    p_upload_dir.add_argument("--names", help="JSON object mapping slot number or stem to a 16-character name")
    p_upload_dir.set_defaults(func=cmd_upload_dir)

    p_verify = sub.add_parser("verify", help="compare .na2l files with the slots")
    p_verify.add_argument("directory")
    p_verify.add_argument("--start", type=int, default=0)
    p_verify.set_defaults(func=cmd_verify)

    p_effects = sub.add_parser("effects", help="list, prepare and deploy custom DSP effects")
    effects = p_effects.add_subparsers(dest="effect_command", required=True)

    p_fx_list = effects.add_parser("list", help="show the shipped custom-effect catalogue")
    p_fx_list.add_argument("--repo", help="path to the re-nanocore checkout")
    p_fx_list.set_defaults(func=lambda a: _run_effects(cmd_effects_list, a))

    p_fx_prepare = effects.add_parser("prepare", help="patch a decoded firmware image (offline)")
    p_fx_prepare.add_argument("--fmu", required=True, help="official .fmu firmware package")
    p_fx_prepare.add_argument("--patch", required=True, help="web patch descriptor JSON")
    p_fx_prepare.add_argument("--output", required=True, help="output OS image path")
    p_fx_prepare.add_argument("--repo", help="path to the re-nanocore checkout")
    p_fx_prepare.set_defaults(func=lambda a: _run_effects(cmd_effects_prepare, a))

    p_fx_backup = effects.add_parser("backup", help="back up all AMP slots and hash them")
    p_fx_backup.add_argument("output", help="new directory for the slot backup")
    p_fx_backup.set_defaults(func=lambda a: _run_effects(cmd_effects_backup, a))

    p_fx_deploy = effects.add_parser("deploy", help="flash a prepared image over raw DFU")
    p_fx_deploy.add_argument("--image", required=True, help="prepared OS image")
    p_fx_deploy.add_argument("--backup-dir", required=True, help="verified 'effects backup' output")
    p_fx_deploy.add_argument("--expect-sha256", required=True, help="installed image hash to expect")
    p_fx_deploy.add_argument("--current-sha256", help="live OS partition hash read before writing")
    p_fx_deploy.add_argument("--repo", help="path to the re-nanocore checkout")
    p_fx_deploy.add_argument("--yes", action="store_true", help="confirm the physical preconditions")
    p_fx_deploy.set_defaults(func=lambda a: _run_effects(cmd_effects_deploy, a))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
