"""Convert WAV files into Nanocore CAB-slot IR data.

What the pedal stores in a CAB slot, measured on firmware 1.2 with 0x50 / 0x56 / 0x59:

  - 30 slots, 0..29
  - 4096 bytes per slot = 1024 float32 little-endian mono samples = 21.33 ms at 48 kHz
  - the slot record is 30 bytes; there is no asset-type byte on the IR side
  - the payload is raw float32 with no header, and the pedal neither resamples nor
    normalises it
  - the pedal enforces the 4096-byte limit itself: a 0x51 begin declaring more is
    refused with status 3 before any data is sent

This converter is a *choice*, not the vendor's algorithm. The vendor app's WAV-to-IR
step is not documented anywhere we could find, and nothing here claims to reproduce
it. What it does:

  1. decode PCM (8/16/24/32-bit) or 32-bit float, average channels to mono
  2. require 48 kHz rather than resample, so the result is never a resample artefact
  3. drop leading samples below 1% of the file's peak, so a silent lead-in does not
     eat the window
  4. keep the first 1024 samples
  5. if the file was longer, fade the last 64 samples with a raised cosine so the
     truncation does not click
  6. normalise the result to a peak of 1.0 and pack it as float32

Step 6 is the one to think about. The pedal does not require normalised input and does
not normalise on write; factory IRs measured 0.72-1.06 peak. Normalising here makes
levels predictable, but it removes the relative loudness between captures, so if you
care about that, pass --no-normalise.

On the ten IRs this was written for, step 4 cost 0.1-2% of each file's energy, because
the sources had already decayed by 21 ms. Check the "energy kept" column it prints: if
a file reports 60%, the pedal is only hearing the first fifth of it, and a shorter
window of the same IR may sound better than a naive truncation.
"""

import argparse
import json
import math
import struct
import sys
from pathlib import Path

TARGET_RATE = 48000
MAX_BYTES = 4096
MAX_SAMPLES = MAX_BYTES // 4  # 1024
FADE = 64
MAX_NAME = 15


def load_wav(path):
    """Return (mono samples as floats in -1..1, sample rate, channels, bits)."""
    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("%s: not a WAV file" % path.name)

    fmt = None
    payload = None
    off = 12
    while off + 8 <= len(data):
        tag = data[off:off + 4]
        size = struct.unpack_from("<I", data, off + 4)[0]
        body = data[off + 8:off + 8 + size]
        if tag == b"fmt " and size >= 16:
            code, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", body)
            if code == 0xFFFE and size >= 40:  # WAVE_FORMAT_EXTENSIBLE
                code = struct.unpack_from("<H", body, 24)[0]
            fmt = (code, channels, rate, bits)
        elif tag == b"data":
            payload = body
        off += 8 + size + (size & 1)

    if fmt is None or payload is None:
        raise ValueError("%s: no fmt/data chunk" % path.name)

    code, channels, rate, bits = fmt
    if code == 3 and bits == 32:
        count = len(payload) // 4
        values = list(struct.unpack("<%df" % count, payload[: count * 4]))
    elif code == 1 and bits == 8:
        values = [(b - 128) / 128.0 for b in payload]
    elif code == 1 and bits == 16:
        count = len(payload) // 2
        values = [v / 32768.0 for v in struct.unpack("<%dh" % count, payload[: count * 2])]
    elif code == 1 and bits == 24:
        raw = payload[: len(payload) // 3 * 3]
        values = [int.from_bytes(raw[i:i + 3], "little", signed=True) / 8388608.0
                  for i in range(0, len(raw), 3)]
    elif code == 1 and bits == 32:
        count = len(payload) // 4
        values = [v / 2147483648.0 for v in struct.unpack("<%di" % count, payload[: count * 4])]
    else:
        raise ValueError("%s: unsupported format code %d / %d-bit" % (path.name, code, bits))

    if channels < 1:
        raise ValueError("%s: %d channels" % (path.name, channels))
    frames = len(values) // channels
    mono = [sum(values[i * channels:(i + 1) * channels]) / channels for i in range(frames)]
    return mono, rate, channels, bits


def onset(samples, peak):
    """First index at or above 1% of the peak, so a silent lead-in is skipped."""
    threshold = 0.01 * peak
    for i, value in enumerate(samples):
        if abs(value) >= threshold:
            return i
    return 0


def derive_name(stem):
    """'01_4x12_Greenback' -> '4x12 Greenback', cut to the 15 characters the pedal takes."""
    text = stem
    while text[:1].isdigit():
        text = text[1:]
    text = text.lstrip(" _-.")
    text = text.replace("_", " ")
    text = "".join(ch for ch in text if 32 <= ord(ch) < 127)
    text = " ".join(text.split())
    return text[:MAX_NAME].rstrip()


def convert(path, start_slot, names, normalise):
    samples, rate, channels, bits = load_wav(path)
    if rate != TARGET_RATE:
        raise ValueError("%s: %d Hz; resampling is not implemented, convert to 48 kHz first"
                         % (path.name, rate))

    peak_in = max(abs(x) for x in samples) if samples else 0.0
    if peak_in == 0.0:
        raise ValueError("%s: silent file" % path.name)

    first = onset(samples, peak_in)
    trimmed = samples[first:]
    count = min(MAX_SAMPLES, len(trimmed))
    out = trimmed[:count]

    truncated = len(trimmed) > count
    if truncated:
        fade = min(FADE, count)
        for i in range(fade):
            out[count - fade + i] *= 0.5 + 0.5 * math.cos(math.pi * (i + 1) / fade)

    # Energy is measured on the window as it came out of the file, before normalising:
    # scaling to 1.0 would otherwise report more than 100% of the source's energy.
    total_e = sum(x * x for x in samples) or 1.0
    kept_e = sum(x * x for x in out)

    if normalise:
        peak = max(abs(x) for x in out) or 1.0
        out = [x / peak for x in out]

    data = struct.pack("<%df" % len(out), *out)
    if len(data) != MAX_BYTES:
        raise ValueError("%s: produced %d bytes, expected %d" % (path.name, len(data), MAX_BYTES))

    name = names.get(path.stem) or derive_name(path.stem)
    return {
        "file": path.name,
        "slot": start_slot,
        "name": name,
        "source": {"rate": rate, "channels": channels, "bits": bits, "frames": len(samples)},
        "onset_samples": first,
        "samples_written": len(out),
        "bytes": len(data),
        "seconds": round(len(out) / TARGET_RATE, 5),
        "truncated": truncated,
        "normalised": normalise,
        "peak_in": round(peak_in, 4),
        "energy_kept_pct": round(100.0 * kept_e / total_e, 2),
        "out": path.stem + ".ir.bin",
        "data": data,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert WAV files to Nanocore CAB-slot IR data (writes one .ir.bin "
                    "per input plus ir_build.json for install_ir.py).")
    parser.add_argument("directory", help="folder of 48 kHz WAV files, taken in filename order")
    parser.add_argument("-o", "--output", default="ir_build", help="output folder (default: ir_build)")
    parser.add_argument("--start", type=int, default=0, help="first CAB slot (default: 0)")
    parser.add_argument("--names", default=None,
                        help="JSON object mapping WAV stem to a name of at most 15 characters")
    parser.add_argument("--no-normalise", dest="normalise", action="store_false",
                        help="keep the recording's own peak instead of scaling to 1.0")
    parser.add_argument("--max-name", type=int, default=MAX_NAME, help="name length limit (default: 15)")
    args = parser.parse_args(argv)

    source = Path(args.directory)
    if not source.is_dir():
        raise SystemExit("%s is not a folder" % source)
    files = sorted(p for p in source.iterdir() if p.suffix.lower() in (".wav", ".wave"))
    if not files:
        raise SystemExit("no .wav files in %s" % source)

    names = {}
    if args.names:
        names = json.loads(Path(args.names).read_text(encoding="utf-8-sig"))

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = []
    for index, path in enumerate(files):
        entry = convert(path, args.start + index, names, args.normalise)
        data = entry.pop("data")
        if len(entry["name"]) > args.max_name:
            raise SystemExit("%s: name %r is longer than %d characters"
                             % (path.name, entry["name"], args.max_name))
        (out_dir / entry["out"]).write_bytes(data)
        report.append(entry)
        print("%-30s slot %02d  %-15s onset %4d  %4d samples  peak %.3f  energy kept %5.1f%%%s"
              % (path.name, entry["slot"], entry["name"], entry["onset_samples"],
                 entry["samples_written"], entry["peak_in"], entry["energy_kept_pct"],
                 "  [truncated]" if entry["truncated"] else ""))

    manifest = out_dir / "ir_build.json"
    manifest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nwrote %d files to %s" % (len(report), out_dir))
    print("next: install_ir.py upload %s --yes" % out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
