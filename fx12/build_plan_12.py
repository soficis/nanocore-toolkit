#!/usr/bin/env python3
"""Build a patch_image.py ("raw-os-fit") plan from patch-1.2.json and prove equivalence.

patch_image.py recomputes the FIT header hashes itself, so the plan carries only the six
code patches. If patch_image(readback, plan) reproduces our partition byte for byte, the
hash bookkeeping is confirmed by an independent implementation.
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get(
    'NANOCORE_REPO', Path.home() / 'nanocore_tools' / 'repos' / 're-nanocore')).expanduser()
REPO_TOOLS = REPO / 'tools' / 'custom_fx'
ROOT = Path(os.environ.get('NANOCORE_WORK', Path.cwd())).expanduser()
READBACK = Path(os.environ.get('NANOCORE_READBACK',
                               ROOT / 'nanocore-os-readback.bin')).expanduser()
OS_IMAGE = ROOT / 'build_out/nanocore-1.2-univibe-os.bin'
PLAN = ROOT / 'build_out/patch-1.2-plan.json'
sys.path.insert(0, str(REPO_TOOLS))
from patch_image import extract_fit, patch_image, sha256  # noqa: E402

PURPOSE = {
    0x08b0ea: 'Velvet DSP body: univibe payload + slot fill',
    0x08a27c: '',   # unused
    0x0a1a7c: 'Effect name "VELVET VIBRATO" -> "UNIVIBE"',
    0x08b06a: 'Defaults writer: UniVibe defaults + SPEED/MODE labels',
    0x044996: 'MOD subtype-10 formatter: percentage + CHORUS/VIBRATO',
    0x0a2c6c: 'Uppercase label row: SPEED, DEPTH, LEVEL, MODE',
    0x0a297c: 'Titlecase label row: SPEED, DEPTH, LEVEL, MODE',
}


def main():
    desc = json.loads((ROOT / 'patch-1.2.json').read_text())
    src = READBACK.read_bytes()
    fit = extract_fit(src)
    print('extract_fit image_version:', repr(fit['image_version']))
    print('source sha256            :', sha256(src))

    patches = []
    for p in desc['patches']:
        patches.append({'offset': p['offset'],
                        'expected_hex': p['beforeHex'],
                        'replacement_hex': p['afterHex'],
                        'description': PURPOSE.get(p['offset'], '')})
    plan = {'image_kind': 'raw-os-fit',
            'firmware_version': fit['image_version'],
            'input_sha256': sha256(src),
            'patches': patches}
    PLAN.write_text(json.dumps(plan, indent=2) + '\n')

    rebuilt, proved = patch_image(src, plan)
    ours = OS_IMAGE.read_bytes()
    print(f'plan written: {PLAN}')
    print('patch_image output == our partition:', rebuilt == ours)
    if rebuilt != ours:
        diff = [i for i in range(len(ours)) if rebuilt[i] != ours[i]]
        print(f'  {len(diff)} differing bytes, first at {diff[0]:#x}' if diff else '')
        return 1
    # patch_image recomputes the FIT header itself, so the header bookkeeping is
    # confirmed by an independent implementation rather than by our own builder.
    # (verify_manifest() is not the right check here: it requires a manifest that
    # patch_image itself produced, not a hand-written plan.)
    if not proved.get('all_fit_hashes_verified'):
        print('FIT hashes did not verify against the rebuilt image')
        return 1
    print('plan output sha256:', sha256(rebuilt))
    print('plan changed_ranges:', proved['changed_ranges'])
    print('all_fit_hashes_verified:', proved['all_fit_hashes_verified'],
          '| padding_and_tail_preserved:', proved['padding_and_tail_preserved'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
