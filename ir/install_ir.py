"""Back up, install and verify Nanocore CAB-slot (IR) data over USB MIDI.

Command map, measured on firmware 1.2:

  0x50 info       -> 20 bytes; u32 slot count @8, u32 bytes-per-slot @12, u16 active @16
  0x51 begin      -> u32 (length | slot<<24); refused with status 3 if length > 4096
  0x52 chunk      -> u32 (offset | slot<<24) + data, 64-byte chunks on the vendor app path
  0x53 commit     -> u32 (length | slot<<24) + u32 zlib CRC-32 over the whole slot
  0x54 abort      -> empty payload
  0x55 name       -> slot u8 + printable ASCII, at most 15 characters
  0x56 slot info  -> 30 bytes on the IR side (no asset-type byte, unlike AMP's 31)
  0x57 apply      -> slot u8, sent after the name by the vendor app; not required for the
                     data to land, purpose still unknown
  0x59 read       -> slot u8, offset u16, length u16; reply slot u8, offset u16, total u16, data

Payload is raw little-endian float32 mono samples, at most 4096 bytes (1024 samples).
The pedal does not resample and does not normalise; it also does not reject a short
slot, so a partial IR is legal (factory slots 11 and 15 are 695 and 941 samples).

Everything here is read-back verified: writes are compared byte-for-byte against what
was sent, in the same session, and `verify` re-checks in a fresh process.
"""

import argparse
import json
import struct
import sys
import time
from pathlib import Path

# nanocore_cli.py holds the SysEx transport, framing and CRC helper. Look next to this
# file first (flat checkout), then in ../cli (repository layout).
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE, _HERE.parent / "cli"):
    if (_candidate / "nanocore_cli.py").exists():
        sys.path.insert(0, str(_candidate))
        break
import nanocore_cli as nc  # noqa: E402

IR_INFO = 0x50
IR_BEGIN = 0x51
IR_CHUNK = 0x52
IR_COMMIT = 0x53
IR_ABORT = 0x54
IR_NAME = 0x55
IR_SLOT_INFO = 0x56
IR_APPLY = 0x57
IR_READ = 0x59

CHUNK = 64
SLOT_COUNT = 30
MAX_BYTES = 4096
MAX_NAME = 15


def storage(nano):
    status, payload = nano.request(IR_INFO, b"", 3.0)
    if status != 0 or len(payload) < 18:
        raise RuntimeError("IR storage info failed: status %d, %d bytes" % (status, len(payload)))
    return {
        "slot_count": struct.unpack_from("<I", payload, 8)[0],
        "max_bytes": struct.unpack_from("<I", payload, 12)[0],
        "active": int.from_bytes(payload[16:18], "little"),
    }


def slot_info(nano, slot):
    status, payload = nano.request(IR_SLOT_INFO, bytes([slot]), 2.0)
    if status != 0:
        raise RuntimeError("IR slot %d info failed with status %d" % (slot, status))
    if len(payload) < 14:
        raise RuntimeError("IR slot %d info is only %d bytes" % (slot, len(payload)))
    return {
        "slot": slot,
        "length": len(payload),
        "present": payload[1],
        "active": payload[2],  # per-slot flag; the active *index* is 0x50 offset 16
        "size": struct.unpack_from("<I", payload, 3)[0],
        "checksum": struct.unpack_from("<I", payload, 7)[0],
        "format_id": int.from_bytes(payload[11:13], "little"),
        "name": payload[14:30].decode("ascii", "replace").rstrip(" \x00") if len(payload) >= 30 else "",
    }


def read_slot(nano, slot, size):
    data = bytearray()
    offset = 0
    while offset < size:
        length = min(200, size - offset)
        status, reply = nano.request(IR_READ, struct.pack("<BHB", slot, offset, length), 2.0)
        if status != 0:
            raise RuntimeError("read slot %d at %d failed with status %d" % (slot, offset, status))
        got_slot, got_offset, total = struct.unpack_from("<BHH", reply)
        chunk = reply[5:]
        if got_slot != slot or got_offset != offset or total != size:
            raise RuntimeError("read reply mismatch for slot %d: %d/%d/%d != %d/%d/%d"
                               % (slot, got_slot, got_offset, total, slot, offset, size))
        data.extend(chunk)
        if not chunk:
            break
        offset += len(chunk)
    if len(data) != size:
        raise RuntimeError("read %d of %d bytes from slot %d" % (len(data), size, slot))
    return bytes(data)


def write_slot(nano, slot, data, name, apply=True):
    if len(data) > MAX_BYTES or len(data) % 4:
        raise ValueError("IR data must be a multiple of 4 bytes and at most %d" % MAX_BYTES)
    if len(name) > MAX_NAME:
        raise ValueError("name %r is longer than %d characters" % (name, MAX_NAME))
    checksum = nc.crc32(data)

    status, _ = nano.request(IR_BEGIN, struct.pack("<I", len(data) | (slot << 24)), 2.0)
    if status != 0:
        raise RuntimeError("begin failed with status %d (the pedal enforces its own size limit)"
                           % status)

    try:
        for offset in range(0, len(data), CHUNK):
            piece = data[offset:offset + CHUNK]
            payload = struct.pack("<I", offset | (slot << 24)) + piece
            last = None
            for _attempt in range(5):
                try:
                    status, _ = nano.request(IR_CHUNK, payload, 0.5)
                except TimeoutError as exc:
                    last = exc
                    time.sleep(0.04)
                    continue
                if status != 0:
                    raise RuntimeError("chunk at %d failed with status %d" % (offset, status))
                last = None
                break
            else:
                raise RuntimeError("chunk at %d timed out 5 times (%s)" % (offset, last))
            time.sleep(0.003)

        status, _ = nano.request(IR_COMMIT,
                                 struct.pack("<I", len(data) | (slot << 24))
                                 + struct.pack("<I", checksum), 3.0)
        if status != 0:
            raise RuntimeError("commit failed with status %d%s"
                               % (status, " (CRC mismatch)" if status == 5 else ""))
    except Exception:
        try:
            nano.request(IR_ABORT, b"", 0.3)
        except Exception:
            pass
        raise

    status, _ = nano.request(IR_NAME, bytes([slot]) + name.encode("ascii"), 2.0)
    if status != 0:
        raise RuntimeError("name failed with status %d" % status)

    if apply:
        try:
            status, _ = nano.request(IR_APPLY, bytes([slot]), 0.8)
            if status != 0:
                print("    note: 0x57 apply returned status %d (continuing)" % status)
        except TimeoutError:
            print("    note: 0x57 apply timed out (continuing)")

    info = slot_info(nano, slot)
    if info["size"] != len(data) or info["checksum"] != checksum:
        raise RuntimeError("slot %d reports %d bytes crc %08x, expected %d crc %08x"
                           % (slot, info["size"], info["checksum"], len(data), checksum))
    return info


def cmd_backup(args):
    nano = nc.Nano(port=args.port, verbose=False)
    info = storage(nano)
    print("IR storage: %d slots, %d bytes each, active slot %d"
          % (info["slot_count"], info["max_bytes"], info["active"]))
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"storage": info, "slots": []}
    for slot in range(min(info["slot_count"], SLOT_COUNT)):
        entry = slot_info(nano, slot)
        if entry["present"] and entry["size"]:
            data = read_slot(nano, slot, entry["size"])
            actual = nc.crc32(data)
            if actual != entry["checksum"]:
                raise RuntimeError("slot %d CRC mismatch: %08x != %08x"
                                   % (slot, actual, entry["checksum"]))
            path = out / ("IR_FACTORY_SLOT_%02d.bin" % slot)
            path.write_bytes(data)
            entry["file"] = path.name
            entry["crc_verified"] = True
        else:
            entry["file"] = None
            entry["crc_verified"] = None
        manifest["slots"].append(entry)
        print("  slot %02d %-16r %5d B  crc %08x  -> %s"
              % (slot, entry["name"], entry["size"], entry["checksum"], entry["file"]))
    (out / "IR_FACTORY_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("\nbacked up %d slots to %s"
          % (sum(1 for s in manifest["slots"] if s["file"]), out))
    return 0


def cmd_upload(args):
    source = Path(args.directory)
    if not source.is_dir():
        raise SystemExit("%s is not a folder" % source)
    table_path = source / "ir_build.json"
    if not table_path.exists():
        raise SystemExit("no ir_build.json in %s; run wav_to_ir.py first" % source)
    table = json.loads(table_path.read_text(encoding="utf-8-sig"))
    if not table:
        raise SystemExit("ir_build.json is empty")

    backup = Path(args.require_backup)
    if not (backup / "IR_FACTORY_MANIFEST.json").exists():
        raise SystemExit("refusing to write: no factory IR backup manifest in %s "
                         "(run install_ir.py backup --output %s first)" % (backup, backup))

    nano = nc.Nano(port=args.port, verbose=False)
    info = storage(nano)
    print("IR storage: %d slots, %d bytes each, active slot %d"
          % (info["slot_count"], info["max_bytes"], info["active"]))
    if info["max_bytes"] < MAX_BYTES:
        raise SystemExit("pedal limit is %d bytes, below the %d planned"
                         % (info["max_bytes"], MAX_BYTES))

    plan = []
    for entry in table:
        path = source / entry["out"]
        if not path.exists():
            raise SystemExit("missing %s for slot %d" % (path, entry["slot"]))
        data = path.read_bytes()
        if len(data) > info["max_bytes"]:
            raise SystemExit("slot %d file is %d bytes, pedal limit is %d"
                             % (entry["slot"], len(data), info["max_bytes"]))
        plan.append((entry["slot"], path, entry["name"], data))

    print("\n%4s  %-15s %5s  %8s  file" % ("slot", "name", "bytes", "crc"))
    for slot, path, name, data in plan:
        print("%4d  %-15s %5d  %08x  %s" % (slot, name, len(data), nc.crc32(data), path.name))

    if not args.yes:
        print("\ndry run: nothing written. Re-run with --yes to install.")
        return 0

    print()
    for slot, path, name, data in plan:
        print("slot %02d: writing %r (%d bytes, crc %08x)"
              % (slot, name, len(data), nc.crc32(data)))
        write_slot(nano, slot, data, name, apply=not args.skip_apply)
        back = read_slot(nano, slot, len(data))
        if back != data:
            raise RuntimeError("slot %d read back differently from what was written" % slot)
        print("  verified: %d bytes identical, crc %08x" % (len(back), nc.crc32(back)))
    print("\nall slots written and verified")
    return 0


def cmd_verify(args):
    source = Path(args.directory)
    table_path = source / "ir_build.json"
    if not table_path.exists():
        raise SystemExit("no ir_build.json in %s" % source)
    table = json.loads(table_path.read_text(encoding="utf-8-sig"))
    nano = nc.Nano(port=args.port, verbose=False)
    ok = 0
    for entry in table:
        slot = entry["slot"]
        info = slot_info(nano, slot)
        expected = (source / entry["out"]).read_bytes()
        same = info["size"] == len(expected) and info["checksum"] == nc.crc32(expected)
        detail = "match"
        if same:
            data = read_slot(nano, slot, info["size"])
            same = data == expected
            detail = "bytes identical" if same else "CRC matches but bytes differ"
        print("slot %02d %-16r %5d B crc %08x  %s (%s)"
              % (slot, info["name"], info["size"], info["checksum"],
                 "OK" if same else "MISMATCH", detail))
        ok += 1 if same else 0
    print("\n%d/%d slots verified" % (ok, len(table)))
    return 0 if ok == len(table) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Back up, install and verify Nanocore CAB-slot (IR) data over USB MIDI.")
    parser.add_argument("--port", default=None, help="MIDI port name (default: the Nanocore port)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("backup", help="read and save every existing IR slot")
    p.add_argument("--output", required=True, help="folder for the slot files and manifest")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("upload", help="write the built .ir.bin files to their slots")
    p.add_argument("directory", help="output folder from wav_to_ir.py")
    p.add_argument("--require-backup", required=True,
                   help="folder holding IR_FACTORY_MANIFEST.json; the write is refused without it")
    p.add_argument("--yes", action="store_true", help="actually write; otherwise prints the plan")
    p.add_argument("--skip-apply", action="store_true",
                   help="do not send 0x57 (measured as not required for the data to land)")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("verify", help="compare every slot against the built files")
    p.add_argument("directory", help="output folder from wav_to_ir.py")
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
