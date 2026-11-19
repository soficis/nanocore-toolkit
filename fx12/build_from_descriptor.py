#!/usr/bin/env python3
"""Build a firmware image from any 1.2 patch descriptor and your own readback.

Schema 1 and schema 2 are both accepted. The descriptor decides the result;
this script never guesses an offset. It refuses to write an image unless the
input classifies as that descriptor's stock image and the output classifies as
its installed image.

    python fx12/build_from_descriptor.py \
        --descriptor web/patch-1.2.json \
        --stock nanocore-os-readback.bin \
        --out univibe-<hash>.bin

Add --expect-sha256 to assert a published full-image hash, or --expect-fit to
assert the descriptor's installed FIT hash.
"""
import argparse
import sys
from pathlib import Path

import descriptor_12 as d12


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--descriptor", required=True, type=Path)
    parser.add_argument("--stock", required=True, type=Path,
                        help="complete 868,352-byte OS partition read from your pedal")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--expect-sha256", help="fail unless the full image has this sha256")
    parser.add_argument("--expect-fit", help="fail unless the installed FIT prefix has this sha256")
    parser.add_argument("--allow-fit-mismatch", action="store_true",
                        help="schema 1 only: accept a timestamp-dependent FIT hash mismatch")
    args = parser.parse_args()

    d = d12.load(args.descriptor)
    stock = args.stock.read_bytes()
    if not stock.startswith(d12.MAGIC):
        sys.exit("input is not an OS partition (no FIT header)")
    try:
        image = d12.apply(stock, d)
    except ValueError as exc:
        sys.exit(str(exc))

    fit_size = d["fitSize"] if d["schemaVersion"] == 1 else d["installedFitSize"]
    fit_sha = d12.sha256(image[:fit_size])
    published = d.get("installedFitSha256")
    if published and fit_sha != published and not args.allow_fit_mismatch:
        sys.exit(f"installed FIT is {fit_sha}, descriptor says {published}; "
                 "the input timestamp probably differs from the one it was built against")

    image_sha = d12.sha256(image)
    if args.expect_fit and fit_sha != args.expect_fit:
        sys.exit(f"installed FIT is {fit_sha}, expected {args.expect_fit}")
    if args.expect_sha256 and image_sha != args.expect_sha256:
        sys.exit(f"image is {image_sha}, expected {args.expect_sha256}")

    changed = sum(1 for off in range(0, d["osSize"], 4096)
                  if image[off:off + 4096] != stock[off:off + 4096])
    args.out.write_bytes(image)

    print(f"effect            {d['effect']} (firmware {d['firmwareVersion']}, schema {d['schemaVersion']})")
    print(f"descriptor        {d12.sha256(args.descriptor.read_bytes())}")
    print(f"image sha256      {image_sha}")
    print(f"installed FIT     {fit_size} bytes, sha256 {fit_sha}")
    print(f"changed blocks    {changed} of {d['osSize'] // 4096}")
    print(f"wrote             {args.out}")


if __name__ == "__main__":
    main()
