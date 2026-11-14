#!/usr/bin/env python3
"""Compare a WSL/Xuantie build artifact against a shipped NanoCore patch body.

Reads web/patch-1.12.json (or --patch) patches[0].afterHex, hashes the whole body and
its prefix, and compares byte-for-byte with the built .bin, reporting differing offsets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

REPO = Path(os.environ.get(
    'NANOCORE_REPO', Path.home() / 'nanocore_tools' / 'repos' / 're-nanocore')).expanduser()


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--bin', required=True, help='built payload .bin')
    p.add_argument('--patch', required=True, help='patch json, relative to repo or absolute')
    p.add_argument('--expect-body-sha256', default=None)
    p.add_argument('--out-txt', default=None, help='write a plaintext report here')
    a = p.parse_args()

    patch = Path(a.patch)
    if not patch.is_absolute():
        patch = REPO / patch
    doc = json.loads(patch.read_text())
    body = bytes.fromhex(doc['patches'][0]['afterHex'])
    built = Path(a.bin).read_bytes()

    lines = []
    def say(s=''):
        print(s)
        lines.append(s)

    say(f'patch file        : {patch}')
    say(f'shipped body len  : {len(body)} bytes  sha256 {sha(body)}')
    if a.expect_body_sha256:
        ok = sha(body) == a.expect_body_sha256
        say(f'expected body hash: {a.expect_body_sha256}  -> {"MATCH" if ok else "MISMATCH"}')
    say(f'built payload len : {len(built)} bytes  sha256 {sha(built)}')

    ref = body[:len(built)]
    say(f'shipped prefix    : first {len(ref)} bytes  sha256 {sha(ref)}')
    same = ref == built
    say(f'EXACT EQUALITY    : {"YES" if same else "NO"}')

    if not same:
        diffs = [i for i in range(min(len(ref), len(built))) if ref[i] != built[i]]
        say(f'differing bytes   : {len(diffs)} of {min(len(ref),len(built))}')
        if diffs:
            say(f'first diff offset : 0x{diffs[0]:x} ({diffs[0]})')
            say(f'last  diff offset : 0x{diffs[-1]:x} ({diffs[-1]})')
            runs = []
            start = prev = diffs[0]
            for i in diffs[1:]:
                if i == prev + 1:
                    prev = i
                    continue
                runs.append((start, prev))
                start = prev = i
            runs.append((start, prev))
            say(f'diff runs         : {len(runs)}')
            for s, e in runs[:40]:
                say(f'  [{s:#06x}..{e:#06x}] len {e-s+1}')
                say(f'    built  : {built[s:e+1].hex(" ")}')
                say(f'    shipped: {ref[s:e+1].hex(" ")}')
        # longest common prefix, to say where the bodies diverge structurally
        n = 0
        while n < min(len(ref), len(built)) and ref[n] == built[n]:
            n += 1
        say(f'common prefix len : {n} of {min(len(ref),len(built))}')

    if a.out_txt:
        Path(a.out_txt).write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
