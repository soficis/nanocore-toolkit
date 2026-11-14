#!/usr/bin/env python3
"""Self-audit of patch-1.2.json against the decoded 1.2 image (offline)."""
import hashlib
import json
import os
import struct
import sys
import zlib
from pathlib import Path
import nc12

ROOT = Path(os.environ.get('NANOCORE_WORK', Path.cwd())).expanduser()
DESC = ROOT / 'patch-1.2.json'
FAIL = []


def check(cond, msg):
    print(('  OK   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAIL.append(msg)


def main():
    d = json.loads(DESC.read_text())
    img = nc12.image()
    fit_size, os_size = d['fitSize'], d['osSize']

    print('== envelope')
    check(d['schemaVersion'] == 1, 'schemaVersion 1')
    check(d['firmwareVersion'] == '1.2' and d['imageVersion'] == '1.2', 'version 1.2')
    check(d['effect'] == 'UNIVIBE' and d['replaces'] == 'VELVET VIBRATO', 'effect/replaces')
    check(os_size == 868352 and fit_size == 759808, f'osSize {os_size} fitSize {fit_size}')
    check(len(img) == fit_size, 'decoded payload is the whole FIT')

    print('== ranges')
    ranges = [(d['timestamp']['offset'], d['timestamp']['size'])]
    check(d['timestamp']['size'] == 4, 'timestamp is 4 bytes')
    entries = [(p['offset'], len(p['beforeHex']) // 2, 'patch') for p in d['patches']] + \
              [(h['offset'], len(h['beforeHex']) // 2, 'hash') for h in d['hashes']]
    for off, size, kind in entries:
        check(0 <= off and size > 0 and off + size <= fit_size, f'{kind} {off:#08x}+{size} inside FIT')
        if len(bytes.fromhex(next(e['beforeHex'] for e in d['patches'] + d['hashes'] if e['offset'] == off))) != size:
            check(False, 'hex length')
    for i, (off, size, _) in enumerate(entries):
        for off2, size2, _ in entries[i + 1:]:
            check(not (off < off2 + size2 and off2 < off + size),
                  f'no overlap {off:#08x} vs {off2:#08x}')
    for off, size, kind in entries:
        check(not (off < d['timestamp']['offset'] + 4 and d['timestamp']['offset'] < off + size),
              f'{kind} {off:#08x} clear of timestamp')

    print('== before/after length + beforeHex vs image')
    for p in d['patches']:
        b, a = bytes.fromhex(p['beforeHex']), bytes.fromhex(p['afterHex'])
        check(len(a) == len(b), f'patch {p["offset"]:#08x} keeps size ({len(b)} B)')
        check(img[p['offset']:p['offset'] + len(b)] == b,
              f'patch {p["offset"]:#08x} beforeHex == image')
    for h in d['hashes']:
        b, a = bytes.fromhex(h['beforeHex']), bytes.fromhex(h['afterHex'])
        check(len(a) == len(b), f'hash {h["segment"]} {h["algorithm"]} keeps size')
        check(img[h['offset']:h['offset'] + len(b)] == b,
              f'hash {h["segment"]} {h["algorithm"]} beforeHex == image')

    print('== rebuild and re-hash')
    out = bytearray(img)
    for e in d['patches'] + d['hashes']:
        b = bytes.fromhex(e['afterHex'])
        out[e['offset']:e['offset'] + len(b)] = b
    check(hashlib.sha256(img[:fit_size]).hexdigest() == d['stockFitSha256'], 'stockFitSha256')
    check(hashlib.sha256(bytes(out[:fit_size])).hexdigest() == d['installedFitSha256'],
          'installedFitSha256')
    norm = bytearray(img[:fit_size])
    for off, size in ranges + [(e[0], e[1]) for e in entries]:
        norm[off:off + size] = bytes(size)
    check(hashlib.sha256(bytes(norm)).hexdigest() == d['normalizedFitSha256'],
          'normalizedFitSha256 (timestamp + every patch/hash range zeroed)')

    print('== segments')
    for s in d['segments']:
        stock = img[s['offset']:s['offset'] + s['size']]
        inst = bytes(out[s['offset']:s['offset'] + s['size']])
        check(hashlib.sha256(stock).hexdigest() == s['stockSha256'], f'{s["name"]} stockSha256')
        check(hashlib.sha256(inst).hexdigest() == s['installedSha256'], f'{s["name"]} installedSha256')
        algs = sorted(h['algorithm'] for h in d['hashes'] if h['segment'] == s['name'])
        check(algs == ['crc32', 'md5'], f'{s["name"]} has crc32+md5 fields')

    print('== FIT header hash fields vs patched segment data')
    for h in d['hashes']:
        s = next(x for x in d['segments'] if x['name'] == h['segment'])
        data = bytes(out[s['offset']:s['offset'] + s['size']])
        want = (struct.pack('>I', zlib.crc32(data)).hex() if h['algorithm'] == 'crc32'
                else hashlib.md5(data).hexdigest())
        check(h['afterHex'] == want, f'{h["segment"]} {h["algorithm"]} afterHex == patched data')

    print('== body patch content')
    body = next(p for p in d['patches'] if p['offset'] == 0x8b0ea)
    bb = bytes.fromhex(body['beforeHex'])
    ba = bytes.fromhex(body['afterHex'])
    check(len(bb) == 1366, f'original body is 1366 B (got {len(bb)})')
    check(bb == img[0x8b0ea:0x8b0ea + 1366], 'original body == image at 0x8b0ea')
    check(len(ba) == 1366, 'replacement is 1366 B')
    payload = ba[:1106]
    check(hashlib.sha256(payload).hexdigest() ==
          '35679d622b3c263810e92fadc294a8c295776d1364fee8c7060dab5ff4aa8237',
          'payload is the proven univibe build')
    check(ba[1106:1298] == b'\x02\x90' * 96, 'bytes 1106..1298 are c.ebreak fill')
    check(ba[1298:] == b'\x02\x90' * 34, 'bytes 1298..1366 are c.ebreak fill (no enum block in 1.2)')

    print('== formatter patch content (MOD subtype-10 region)')
    fmt = next(p for p in d['patches'] if p['offset'] == 0x44996)
    fb = bytes.fromhex(fmt['beforeHex'])
    fa = bytes.fromhex(fmt['afterHex'])
    blob = (ROOT / 'ui12/out/vibe_format.bin').read_bytes()
    check(len(fb) == 68 and len(fa) == 68, f'formatter patch is 68 B (before {len(fb)}, after {len(fa)})')
    check(nc12.va(0x44996) == 0x4002d196, 'formatter patch at vaddr 0x4002d196')
    check(fa[:len(blob)] == blob, f'first {len(blob)} B are the assembled blob')
    check(fa[len(blob):] == b'\x02\x90' * ((68 - len(blob)) // 2),
          f'remaining {68 - len(blob)} B are c.ebreak fill')
    check(img[0x44990:0x44996] == bytes.fromhex('a9476394f904'),
          'subtype guard 0x4002d190..0x4002d196 is untouched')
    check(img[0x449da:0x449de] == bytes.fromhex('8d47639f'),
          'subtype-3 arm at 0x4002d1da is untouched')
    check(fb[:2] == bytes.fromhex('1de0'), 'stock region started with the index-0 arm (c.bnez s0)')

    print('== cross-check against the CLI output')
    cli = ROOT / 'build_out/cli_installed.bin'
    if cli.is_file():
        cli_bytes = cli.read_bytes()
        check(len(cli_bytes) == os_size, f'CLI image is {os_size} B')
        check(cli_bytes[:fit_size] == bytes(out[:fit_size]), 'CLI image == our reconstruction')
        check(cli_bytes[fit_size:] == bytes(os_size - fit_size), 'CLI tail beyond FIT is zero')
    else:
        print('  SKIP cli_installed.bin not present')

    print()
    if FAIL:
        print(f'FAILED {len(FAIL)} check(s)')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
