#!/usr/bin/env python3
"""Identify an OS readback against one or more 1.2 patch descriptors, offline.

No device, no USB, no writes. Feed it a saved partition and every descriptor
you trust; it reports exactly which stock or installed image the bytes are.
This is the check to run before choosing an image to flash, and again on the
readback afterwards.

    python fx12/identify_image.py --image backup.bin \
        --descriptor fx12/examples/patch-1.2.json \
        --descriptor octave-1.2.json

Exit codes: 0 one identity, 1 no match, 2 more than one installed identity.
"""
import argparse
import json
import sys
from pathlib import Path

import descriptor_12 as d12


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, type=Path,
                        help="complete 868,352-byte OS partition")
    parser.add_argument("--descriptor", required=True, type=Path, action="append",
                        help="repeat for each descriptor to test against")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    image = args.image.read_bytes()
    image_sha = d12.sha256(image)
    groups = {}
    for path in args.descriptor:
        try:
            d = d12.load(path)
            state = d12.classify(image, d)
        except (ValueError, KeyError) as exc:
            print(f"skipped {path}: {exc}", file=sys.stderr)
            continue
        if not state:
            continue
        fit_size = d["fitSize"] if d["schemaVersion"] == 1 else d["installedFitSize"]
        fit_sha = d12.sha256(image[:fit_size])
        key = (state["kind"], state["version"], state["effect"], fit_sha if state["kind"] == "installed" else "")
        group = groups.setdefault(key, {"state": state, "fit": fit_sha, "paths": [], "note": ""})
        group["paths"].append(str(path))
        if state["kind"] == "installed" and d["schemaVersion"] == 1:
            published = d.get("installedFitSha256")
            if published and published != fit_sha:
                group["note"] = "  (FIT hash differs from descriptor)"

    if args.json:
        print(json.dumps({
            "image": {"bytes": len(image), "sha256": image_sha},
            "matches": [{"kind": g["state"]["kind"], "version": g["state"]["version"],
                         "effect": g["state"]["effect"], "installedFitSha256": g["fit"],
                         "note": g["note"].strip(),
                         "descriptors": g["paths"]} for g in groups.values()],
        }, indent=2))
    else:
        print(f"image  {len(image):,} bytes  sha256 {image_sha}")
        for g in sorted(groups.values(), key=lambda g: g["state"]["kind"] != "installed"):
            state = g["state"]
            line = f"  {state['kind']:<9} {state['effect']:<14} firmware {state['version']}"
            if state["kind"] == "installed":
                line += f"  FIT {g['fit']}{g['note']}"
            print(line)
            for path in g["paths"]:
                print(f"            {path}")

    if not groups:
        print("no descriptor matches this image", file=sys.stderr)
        sys.exit(1)
    installed = [g for g in groups.values() if g["state"]["kind"] == "installed"]
    if len({(g["state"]["version"], g["state"]["effect"], g["fit"]) for g in installed}) > 1:
        print("ambiguous: more than one installed identity; do not flash from this result", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
