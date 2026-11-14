#!/usr/bin/env python3
"""Byte-diff the deployable partition against the pedal readback.

Proves the change set is exactly the 6 patch regions + the 8 FIT-header hash fields
and nothing else, including the 108,544-byte device tail.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get('NANOCORE_WORK', Path.cwd())).expanduser()
READBACK = Path(os.environ.get('NANOCORE_READBACK',
                               ROOT / 'nanocore-os-readback.bin')).expanduser()
PATCHED = ROOT / 'build_out/nanocore-1.2-univibe-os.bin'
DESC = ROOT / 'patch-1.2.json'
FIT = 759808

FAIL = []


def check(cond, msg):
    print(('  OK   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAIL.append(msg)


def runs_of(idxs):
    runs, start, prev = [], None, None
    for i in idxs:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            runs.append((start, prev))
            start = prev = i
    if start is not None:
        runs.append((start, prev))
    return runs


def main():
    a = READBACK.read_bytes()
    b = PATCHED.read_bytes()
    d = json.loads(DESC.read_text())
    check(len(a) == len(b) == 868352, f'both images are 868352 B (readback {len(a)}, patched {len(b)})')

    logical = []
    for i, p in enumerate(d['patches']):
        n = len(p['beforeHex']) // 2
        logical.append((p['offset'], n, f'patch {i}: {purpose(i)}'))
    for h in d['hashes']:
        logical.append((h['offset'], len(h['beforeHex']) // 2,
                        f'FIT hash {h["segment"]} {h["algorithm"]}'))
    logical.sort()
    check(len(logical) == 14, f'{len(logical)} logical ranges (6 patches + 8 hash fields)')

    changed = [i for i in range(len(a)) if a[i] != b[i]]
    print(f'\nchanged bytes: {len(changed)} of {len(a)}  ({len(a) - len(changed)} identical)')
    runs = runs_of(changed)

    def owner(off):
        for start, size, label in logical:
            if start <= off < start + size:
                return label
        return None

    print('\n== contiguous changed runs')
    print(f'{"offset":>10}  {"len":>6}  reason')
    for s, e in runs:
        inside = sorted({owner(i) for i in range(s, e + 1)} - {None})
        print(f'{s:#010x}  {e - s + 1:>6}  {" + ".join(inside) if inside else "*** OUTSIDE THE CHANGE SET ***"}')

    print('\n== write set (14 ranges, the exact bytes the descriptor overwrites)')
    print(f'{"offset":>10}  {"len":>6}  {"changed":>7}  reason')
    written = 0
    for start, size, label in logical:
        n = sum(1 for i in range(start, start + size) if a[i] != b[i])
        written += size
        print(f'{start:#010x}  {size:>6}  {n:>7}  {label}')
        # a range whose after bytes equal the stock bytes is a deliberate no-op
        # (the other segments' FIT hash fields are re-stated, not changed)
        after = next((bytes.fromhex(e['afterHex']) for e in d['patches'] + d['hashes']
                      if e['offset'] == start), b'')
        before = next((bytes.fromhex(e['beforeHex']) for e in d['patches'] + d['hashes']
                       if e['offset'] == start), b'')
        if before == after:
            check(n == 0, f'{label}: identical afterHex, so 0 changed bytes')
        else:
            check(n > 0, f'{label} changes at least one byte')
        check(n <= size, f'{label} changes only inside its own range')
    print(f'  bytes written {written}, bytes actually differing {len(changed)}')

    outside = [i for i in changed if owner(i) is None]
    check(not outside, f'no changed byte outside the 14 ranges ({len(outside)} found)')
    if outside:
        for i in outside[:20]:
            print(f'   extra change at {i:#x}: {a[i]:02x} -> {b[i]:02x}')
    check(not any(i >= FIT for i in changed), 'no changed byte at or beyond fitSize (device tail)')
    check(b[FIT:] == a[FIT:], 'device tail [759808,868352) is byte-identical')

    print('\n== region adjacency')
    print(f'  maximal runs of strictly changed bytes: {len(runs)}')
    print(f'  write ranges (the change set)         : {len(logical)}')
    for (s1, n1, l1), (s2, n2, l2) in zip(logical, logical[1:]):
        if s1 + n1 == s2:
            print(f'  adjacent, so one run can span both: [{s1:#x},{s1+n1:#x}) {l1}')
            print(f'                                      [{s2:#x},{s2+n2:#x}) {l2}')

    print()
    print(f'  stock   sha256 {hashlib.sha256(a).hexdigest()}')
    print(f'  patched sha256 {hashlib.sha256(b).hexdigest()}')

    print()
    if FAIL:
        print(f'FAILED {len(FAIL)} check(s)')
        for f in FAIL:
            print('  -', f)
        return 1
    print('diff is exactly the intended change set')
    return 0


def purpose(i):
    return ['Velvet DSP body 1366 B @0x8b0ea',
            '"VELVET VIBRATO" -> "UNIVIBE" @0xa1a7c',
            'defaults writer 128 B @0x8b06a',
            'MOD subtype-10 formatter 68 B @0x44996',
            'uppercase label row @0xa2c6c',
            'titlecase label row @0xa297c'][i]


if __name__ == '__main__':
    sys.exit(main())
