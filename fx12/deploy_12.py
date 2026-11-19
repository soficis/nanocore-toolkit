#!/usr/bin/env python3
"""Raw-DFU operations for firmware 1.2 images: enter, backup, deploy, restore.

    enter     soft-reboot the pedal into Lupgrader (normal mode, MIDI port up)
    backup    read the whole OS partition and save it with its sha256
    deploy    write an image, authenticated against its descriptor first
    restore   write a saved partition, after an explicit confirmation phrase

Deploy and restore both read the live partition, require it to match
--expected-current, and verify the full readback before manifesting. There is
no retry path: on failure the new directory holds preflight-os.bin,
readback.bin or failure.txt.

The USB transport comes from a re-nanocore checkout; set NANOCORE_WORK to its
root. This script ships no firmware.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import descriptor_12 as d12

OS_SIZE = 868352


def raw_stack():
    repo = Path(os.environ.get("NANOCORE_WORK", Path.cwd())).expanduser()
    for extra in ("tools/custom_fx", "tools/firmware_decode"):
        path = repo / extra
        if not path.is_dir():
            sys.exit(f"{path} not found; clone re-nanocore and set NANOCORE_WORK")
        sys.path.insert(0, str(path))
    from raw_dfu import LibUSB, RawOS, program
    return LibUSB, RawOS, program


def libusb_path(value):
    if value:
        return value
    try:
        import libusb_package
        return libusb_package.get_library_path()
    except ImportError:
        sys.exit("pass --libusb, or pip install libusb-package")


def fresh_dir(path):
    if path.exists():
        sys.exit(f"refusing to overwrite {path}")
    path.mkdir(parents=True)
    return path


def write_report(out, name, data):
    (out / name).write_bytes(data)
    print(f"saved {name} ({len(data)} bytes)")


def program(image, expected, out, libusb):
    LibUSB, RawOS, program_fn = raw_stack()
    transport = LibUSB(libusb_path(libusb))
    device = RawOS(transport)
    try:
        report = program_fn(device, bytes(image), expected,
                            lambda data: write_report(out, "readback.bin", data),
                            lambda data: write_report(out, "preflight-os.bin", data))
    finally:
        transport.close()
    return report


def enter(args):
    try:
        import mido
    except ImportError:
        sys.exit("pip install mido python-rtmidi")
    ports = [name for name in mido.get_output_names() if "Nanocore" in name]
    if len(ports) != 1:
        sys.exit(f"expected one Nanocore MIDI output, found {ports}")
    with mido.open_output(ports[0]) as midi:
        midi.send(mido.Message("sysex", data=[0x7D, 0x44, 0x46, 0x55, 0x01]))
    print(f"sent Lupgrader entry to {ports[0]}; waiting {args.wait}s for 33c3:6678")
    time.sleep(args.wait)


def backup(args):
    LibUSB, RawOS, _ = raw_stack()
    out = fresh_dir(args.out)
    transport = LibUSB(libusb_path(args.libusb))
    device = RawOS(transport)
    try:
        image = device.read_partition()
    finally:
        transport.close()
    if not image.startswith(d12.MAGIC):
        sys.exit("read did not start with a FIT header; nothing saved")
    write_report(out, "os-partition.bin", image)
    report = {"action": "backup", "bytes": len(image), "sha256": d12.sha256(image),
              "fit_verified": True, "device_left_in_dfu": True}
    (out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def deploy(args):
    d = d12.load(args.descriptor)
    image = args.image.read_bytes()
    state = d12.classify(image, d)
    if not state:
        sys.exit("image does not match the descriptor")
    if state["kind"] != "installed":
        sys.exit("image classifies as stock, not as the installed build; refusing to write")
    fit_size = d["fitSize"] if d["schemaVersion"] == 1 else d["installedFitSize"]
    print(f"target {state['effect']} firmware {state['version']}, "
          f"installed FIT {d12.sha256(image[:fit_size])}")
    args.action = "deploy"
    run_program(args, image)


def restore(args):
    image = args.image.read_bytes()
    if len(image) != OS_SIZE:
        sys.exit(f"expected a {OS_SIZE}-byte OS partition")
    if not image.startswith(d12.MAGIC):
        sys.exit("restore input does not start with a FIT header")
    target = d12.sha256(image)
    if args.confirm != f"RESTORE {target[:8]}":
        sys.exit(f'confirmation mismatch; pass --confirm "RESTORE {target[:8]}"')
    args.action = "restore"
    run_program(args, image)


def run_program(args, image):
    out = fresh_dir(args.out)
    try:
        report = program(image, args.expected_current, out, args.libusb)
    except BaseException as exc:
        (out / "failure.txt").write_text(str(exc) + "\n")
        print(f"failed: {exc}", file=sys.stderr)
        sys.exit(1)
    report["action"] = args.action
    (out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)

    e = sub.add_parser("enter", help="reboot into Lupgrader over MIDI SysEx")
    e.add_argument("--wait", type=float, default=6.0)
    e.set_defaults(func=enter)

    b = sub.add_parser("backup", help="read and save the whole OS partition")
    b.add_argument("--out", required=True, type=Path)
    b.add_argument("--libusb")
    b.set_defaults(func=backup)

    d = sub.add_parser("deploy", help="write a descriptor-authenticated image")
    d.add_argument("--descriptor", required=True, type=Path)
    d.add_argument("--image", required=True, type=Path)
    d.add_argument("--expected-current", required=True,
                   help="sha256 of the live partition, from a backup")
    d.add_argument("--out", required=True, type=Path)
    d.add_argument("--libusb")
    d.set_defaults(func=deploy)

    r = sub.add_parser("restore", help="write a saved partition over DFU")
    r.add_argument("--image", required=True, type=Path,
                   help="the saved OS partition to write back")
    r.add_argument("--expected-current", required=True)
    r.add_argument("--confirm", required=True,
                   help='RESTORE plus the first 8 hex digits of the image sha256')
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--libusb")
    r.set_defaults(func=restore)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
