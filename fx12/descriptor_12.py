"""Shared rules for firmware 1.2 patch descriptors.

A descriptor is either schema 1 (a fixed-size in-place patch) or schema 2 (an
in-place trampoline plus bytes appended after the stock FIT). The two formats
normalize differently, and mis-applying one as the other produces an image
neither project built. This module is the one place that knows the difference.

Schema 1 normalization zeroes the timestamp and every patched or hashed range,
so the same hash describes the stock image and the installed image.
Schema 2 normalization zeroes only the timestamp, over the stock or installed
FIT length, so the two hashes are distinct.
"""
import hashlib
import json
from pathlib import Path

MAGIC = bytes.fromhex("d00dfeed")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    d = json.loads(Path(path).read_text())
    if d.get("schemaVersion") not in (1, 2):
        raise ValueError("descriptor is not schema 1 or 2")
    common = ("firmwareVersion", "effect", "osSize", "patches")
    for key in common:
        if key not in d:
            raise ValueError(f"descriptor has no {key}")
    if d["schemaVersion"] == 1:
        for key in ("fitSize", "normalizedFitSha256", "timestamp"):
            if key not in d:
                raise ValueError(f"schema-1 descriptor has no {key}")
    else:
        for key in ("stockFitSize", "installedFitSize", "protectedOffset", "append",
                    "stockNormalizedSha256", "installedNormalizedSha256", "timestamp"):
            if key not in d:
                raise ValueError(f"schema-2 descriptor has no {key}")
    return d


def entries(d):
    """Entries rewritten in place: patches, plus the FIT header hashes."""
    return list(d["patches"]) + (list(d.get("hashes", [])) if d["schemaVersion"] == 1 else [])


def timestamp_range(d):
    t = d["timestamp"]
    return (t["offset"], t["size"])


def normalize(image, size, ranges):
    if len(image) < size:
        raise ValueError("image is shorter than the range being normalized")
    buf = bytearray(image[:size])
    for offset, length in ranges:
        if offset < 0 or offset + length > size:
            raise ValueError(f"normalization range {offset:#x}+{length} is outside the FIT")
        buf[offset:offset + length] = b"\0" * length
    return sha256(buf)


def classify(image, d):
    """Return {kind, version, effect} for an exact match, else None."""
    if len(image) != d["osSize"]:
        return None
    if d["schemaVersion"] == 2:
        for kind in ("stock", "installed"):
            if normalize(image, d[kind + "FitSize"], [timestamp_range(d)]) == d[kind + "NormalizedSha256"]:
                return {"kind": kind, "version": d["firmwareVersion"], "effect": d["effect"]}
        return None

    ranges = [timestamp_range(d)] + [(e["offset"], len(e["beforeHex"]) // 2) for e in entries(d)]
    if normalize(image, d["fitSize"], ranges) != d["normalizedFitSha256"]:
        return None
    for kind in ("stock", "installed"):
        field = "beforeHex" if kind == "stock" else "afterHex"
        try:
            for s in d.get("segments", []):
                key = kind + "Sha256"
                if key in s and sha256(image[s["offset"]:s["offset"] + s["size"]]) != s[key]:
                    raise ValueError
            for e in entries(d):
                expected = bytes.fromhex(e[field])
                if image[e["offset"]:e["offset"] + len(expected)] != expected:
                    raise ValueError
        except ValueError:
            continue
        return {"kind": kind, "version": d["firmwareVersion"], "effect": d["effect"]}
    return None


def apply(stock, d):
    """Apply a descriptor to a stock partition. Returns a new full-size image."""
    state = classify(stock, d)
    if not state or state["kind"] != "stock":
        raise ValueError("input does not match the descriptor's stock image")

    image = bytearray(stock)
    limit = d["fitSize"] if d["schemaVersion"] == 1 else d["stockFitSize"]
    for e in entries(d):
        offset = e["offset"]
        before = bytes.fromhex(e["beforeHex"])
        after = bytes.fromhex(e["afterHex"])
        if len(before) != len(after):
            raise ValueError(f"size change at {offset:#08x}")
        if offset < 0 or offset + len(before) > limit:
            raise ValueError(f"write at {offset:#08x} leaves the patchable FIT")
        if bytes(image[offset:offset + len(before)]) != before:
            raise ValueError(f"beforeHex mismatch at {offset:#08x}")
        image[offset:offset + len(after)] = after

    if d["schemaVersion"] == 2:
        append = d["append"]
        payload = bytes.fromhex(append["afterHex"])
        if append["offset"] != d["stockFitSize"]:
            raise ValueError("append does not start at the stock FIT end")
        if append["offset"] + len(payload) != d["installedFitSize"]:
            raise ValueError("append length disagrees with installedFitSize")
        if append["offset"] + len(payload) > d["protectedOffset"]:
            raise ValueError("append reaches the protected tail")
        image[append["offset"]:append["offset"] + len(payload)] = payload

    out = bytes(image)
    state = classify(out, d)
    if not state or state["kind"] != "installed":
        raise ValueError("patched image does not classify as installed")
    return out
