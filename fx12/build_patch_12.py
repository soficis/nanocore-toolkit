#!/usr/bin/env python3
"""Assemble the firmware-1.2 UniVibe deliverable from the PEDAL'S OWN OS READBACK.

Authoritative input: nanocore-os-readback.bin (868,352 B, full OS partition). Every
hash, segment record and patch byte is computed from that file; the downloadable .fmu
is used only as a cross-check that the readback prefix is the same firmware image.

Offline only: no device access. Outputs patch-1.2.json plus the full patched
partition nanocore-1.2-univibe-os.bin with the device-owned tail preserved exactly.
"""
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path
import nc12

ROOT = Path(os.environ.get('NANOCORE_WORK', Path.cwd())).expanduser()
READBACK = Path(os.environ.get('NANOCORE_READBACK',
                               ROOT / 'nanocore-os-readback.bin')).expanduser()
READBACK_SHA256 = '8ed563f16242610d634c6659cb0002b913a6a8ff89a1ffc9b0a2d4e913a684da'
EXPECTED_PREFIX_SHA256 = 'c45b9d874c051a861bbec6765a288f923e5afe766fcac50d45bf56b95b2a954a'
BODY = (ROOT / 'build_out/12_univibe/univibe.bin').read_bytes()
DEFAULTS = (ROOT / 'ui12/out/vibe_defaults.bin').read_bytes()
FORMAT = (ROOT / 'ui12/out/vibe_format.bin').read_bytes()
OUT = ROOT / 'patch-1.2.json'
OS_IMAGE = ROOT / 'build_out/nanocore-1.2-univibe-os.bin'
BASELINE = ROOT / 'build_out/patch-1.2.fmu-derived.json'

SEG3_OFF = 0x17800
SITE = 0x400738ea
SLOT = 1366
LIMIT = 1298          # linker limit used for the DSP payload
PAD = b'\x02\x90'     # c.ebreak, the filler used by the shipped 1.10/1.12 bodies

# FIT offsets (file offsets in the decoded payload).
def fit(vaddr):
    return vaddr - 0x40000000 + SEG3_OFF

SITES = {
    'body': fit(0x400738ea),         # 0x8b0ea, 1366 B stock Velvet routine
    'defaults': fit(0x4007386a),     # 0x8b06a, 128 B defaults writer slot
    'format': fit(0x4002d196),       # 0x44996, 68 B proven MOD subtype-10 formatter region
    'upper': fit(0x4008b46c),        # 0xa2c6c, uppercase label row (5 entries)
    'title': fit(0x4008b17c),        # 0xa297c, titlecase label row (5 entries)
    'name': fit(0x4008a27c),         # 0xa1a7c, "VELVET VIBRATO" (15 B)
}

FORMAT_REGION = 68                   # 0x4002d196..0x4002d1da, zero slack

HASH_FIELDS = {  # (crc32 offset, md5 offset) per segment, from the FIT header
    'seg0': (0x16c, 0x1a0),
    'seg1': (0x290, 0x2c4),
    'seg2': (0x3b4, 0x3e8),
    'seg3': (0x4d8, 0x50c),
}


def pad_to(b, n, fill=PAD):
    assert len(b) <= n, (len(b), n)
    assert (n - len(b)) % len(fill) == 0, (n - len(b))
    return b + fill * ((n - len(b)) // len(fill))


def main():
    # ---- authoritative source: the pedal's own OS readback --------------------
    full = READBACK.read_bytes()
    assert len(full) == 868352, f'readback is {len(full)} bytes, expected 868352'
    whole = hashlib.sha256(full).hexdigest()
    assert whole == READBACK_SHA256, f'readback sha256 {whole} != {READBACK_SHA256}'
    man = nc12.manifest()
    fit_size, os_size = man['payload_size'], 868352
    img = full[:fit_size]
    assert hashlib.sha256(img).hexdigest() == EXPECTED_PREFIX_SHA256, 'readback FIT prefix hash'
    assert img == nc12.image(), 'readback prefix differs from the decoded fmu payload'

    # ---- segment truth: manifest, re-verified against the readback ------------
    for s in man['segments']:
        data = img[s['offset']:s['offset'] + s['size']]
        crc_off, md5_off = HASH_FIELDS[s['name']]
        assert len(data) == s['size'], s['name']
        assert format(zlib.crc32(data), '08x') == s['hashes_verified']['crc32'], \
            f'{s["name"]} crc32 differs'
        assert hashlib.md5(data).hexdigest() == s['hashes_verified']['md5'], \
            f'{s["name"]} md5 differs'
        assert img[crc_off:crc_off + 4].hex() == s['hashes_verified']['crc32'], \
            f'{s["name"]} FIT header crc32 field differs'
        assert img[md5_off:md5_off + 16].hex() == s['hashes_verified']['md5'], \
            f'{s["name"]} FIT header md5 field differs'

    # ---- body: DSP payload + slot padding -------------------------------------
    assert hashlib.sha256(BODY).hexdigest() == \
        '35679d622b3c263810e92fadc294a8c295776d1364fee8c7060dab5ff4aa8237', 'unexpected payload'
    assert len(BODY) == 1106 < LIMIT
    body_after = pad_to(BODY, LIMIT) + pad_to(b'', SLOT - LIMIT)
    assert len(body_after) == SLOT
    body_before = img[SITES['body']:SITES['body'] + SLOT]
    assert len(body_before) == SLOT

    # ---- defaults writer -------------------------------------------------------
    assert len(DEFAULTS) <= 128
    defaults_after = pad_to(DEFAULTS, 128)
    defaults_before = img[SITES['defaults']:SITES['defaults'] + 128]

    # ---- value formatter (proven MOD subtype-10 region, zero slack) ------------
    assert len(FORMAT) <= FORMAT_REGION, (len(FORMAT), FORMAT_REGION)
    fmt_after = pad_to(FORMAT, FORMAT_REGION)
    fmt_before = img[SITES['format']:SITES['format'] + FORMAT_REGION]
    assert len(fmt_before) == FORMAT_REGION
    # the subtype guard before the region and the subtype-3 arm after it must not be
    # part of the replacement (guard = `li a5,10; bne s3,a5,0x4002d1da`, 6 bytes)
    assert img[fit(0x4002d190):fit(0x4002d196)] == bytes.fromhex('a9476394f904'), 'guard changed'
    assert SITES['format'] == fit(0x4002d196) == 0x44996, SITES['format']
    assert img[SITES['format'] + FORMAT_REGION:SITES['format'] + FORMAT_REGION + 4] == \
        bytes.fromhex('8d47639f'), 'subtype-3 arm not where expected'

    # ---- label rows: 4 of 5 pointers each, MIX (5th) left native ---------------
    speed_label, mode_label = 0x400738a8, 0x400738ae      # inside the defaults blob
    depth_upper, level_upper = 0x4008a604, 0x4008a5a0     # native "DEPTH"/"LEVEL"
    labels = struct.pack('<4I', speed_label, depth_upper, level_upper, mode_label)
    upper_before = img[SITES['upper']:SITES['upper'] + 16]
    title_before = img[SITES['title']:SITES['title'] + 16]

    # ---- name string -----------------------------------------------------------
    name_before = img[SITES['name']:SITES['name'] + 15]
    assert name_before == b'VELVET VIBRATO\0', name_before
    name_after = b'UNIVIBE\0' + bytes(15 - 8)

    patches = [
        {'offset': SITES['body'], 'beforeHex': body_before.hex(), 'afterHex': body_after.hex()},
        {'offset': SITES['name'], 'beforeHex': name_before.hex(), 'afterHex': name_after.hex()},
        {'offset': SITES['defaults'], 'beforeHex': defaults_before.hex(), 'afterHex': defaults_after.hex()},
        {'offset': SITES['format'], 'beforeHex': fmt_before.hex(), 'afterHex': fmt_after.hex()},
        {'offset': SITES['upper'], 'beforeHex': upper_before.hex(), 'afterHex': labels.hex()},
        {'offset': SITES['title'], 'beforeHex': title_before.hex(), 'afterHex': labels.hex()},
    ]

    # ---- apply patches to the FULL partition, then the FIT header hashes --------
    out = bytearray(full)          # keeps the 108,544-byte device tail untouched
    for p in patches:
        b = bytes.fromhex(p['afterHex'])
        out[p['offset']:p['offset'] + len(b)] = b

    segments = []
    hashes = []
    for s in man['segments']:
        name = s['name']
        data = bytes(out[s['offset']:s['offset'] + s['size']])
        stock = bytes(img[s['offset']:s['offset'] + s['size']])
        crc, md5 = HASH_FIELDS[name]
        hashes.append({'offset': crc, 'beforeHex': img[crc:crc + 4].hex(),
                       'afterHex': struct.pack('>I', zlib.crc32(data)).hex(),
                       'segment': name, 'algorithm': 'crc32'})
        hashes.append({'offset': md5, 'beforeHex': img[md5:md5 + 16].hex(),
                       'afterHex': hashlib.md5(data).hexdigest(),
                       'segment': name, 'algorithm': 'md5'})
        segments.append({'name': name, 'offset': s['offset'], 'size': s['size'],
                         'stockSha256': hashlib.sha256(stock).hexdigest(),
                         'installedSha256': hashlib.sha256(data).hexdigest()})

    for h in hashes:
        b = bytes.fromhex(h['afterHex'])
        out[h['offset']:h['offset'] + len(b)] = b

    # ---- the three FIT hashes --------------------------------------------------
    stock_fit = hashlib.sha256(img[:fit_size]).hexdigest()
    installed = hashlib.sha256(bytes(out[:fit_size])).hexdigest()

    ranges = [(76, 4)] + [(p['offset'], len(p['beforeHex']) // 2) for p in patches] \
             + [(h['offset'], len(h['beforeHex']) // 2) for h in hashes]
    normalized = bytearray(img[:fit_size])
    for off, size in ranges:
        normalized[off:off + size] = bytes(size)
    normalized_hex = hashlib.sha256(bytes(normalized)).hexdigest()

    desc = {
        'schemaVersion': 1,
        'firmwareVersion': '1.2',
        'imageVersion': '1.2',
        'effect': 'UNIVIBE',
        'replaces': 'VELVET VIBRATO',
        'osSize': os_size,
        'fitSize': fit_size,
        'stockFitSha256': stock_fit,
        'installedFitSha256': installed,
        'normalizedFitSha256': normalized_hex,
        'timestamp': {'offset': 76, 'size': 4},
        'patches': patches,
        'hashes': hashes,
        'segments': segments,
    }
    OUT.write_text(json.dumps(desc, indent=2) + '\n')

    # ---- the deployable partition --------------------------------------------
    assert len(out) == os_size, f'patched image is {len(out)} bytes'
    assert hashlib.sha256(bytes(out[:fit_size])).hexdigest() == installed, 'installed FIT hash'
    assert bytes(out[fit_size:]) == full[fit_size:], 'device tail was modified'
    OS_IMAGE.write_bytes(bytes(out))

    # ---- the patch payloads must be exactly what the earlier build produced ---
    if BASELINE.is_file():
        base = json.loads(BASELINE.read_text())
        mine = {(p['offset'], len(p['beforeHex']) // 2): (p['beforeHex'], p['afterHex'])
                for p in patches}
        theirs = {(p['offset'], len(p['beforeHex']) // 2): (p['beforeHex'], p['afterHex'])
                  for p in base['patches']}
        assert mine == theirs, 'patch offsets/payloads drifted from the fmu-derived build'
        print('  patch set identical to build_out/patch-1.2.fmu-derived.json')

    print(f'wrote {OUT} and {OS_IMAGE.name} ({len(out)} bytes)')
    print(f'  stockFitSha256      {stock_fit}')
    print(f'  installedFitSha256  {installed}')
    print(f'  normalizedFitSha256 {normalized_hex}')
    print(f'  patches {len(patches)}  hashes {len(hashes)}  segments {len(segments)}')
    for p in patches:
        print(f'   off {p["offset"]:#08x} vaddr {nc12.va(p["offset"]):#010x} '
              f'{len(p["beforeHex"])//2:5d} B')
    for h in hashes:
        flag = '' if h['beforeHex'] == h['afterHex'] else '  <- updated'
        print(f'   hash {h["segment"]} {h["algorithm"]:5s} @{h["offset"]:#06x}'
              f' {h["beforeHex"]} -> {h["afterHex"]}{flag}')


if __name__ == '__main__':
    main()
