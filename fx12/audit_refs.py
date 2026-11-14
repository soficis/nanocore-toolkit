#!/usr/bin/env python3
"""Audit every reference in the *code* part of a linked NanoCore FX payload.

rodata is data, not code, so the audit range must stop where .rodata begins (take it from
the linker map: .text.nc_fx_univibe size). Every branch/jal target, every auipc/lui page
resolved through its following load/store (objdump's "# 0x..." comment), and every literal
address-looking constant is classified INTERNAL (inside .payload -> a re-link at another
address moves it) or EXTERNAL (a fixed address that must exist in the target firmware).
"""
import argparse
import re

LINE = re.compile(r'^\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(?:(\S+)\s*(.*))?$')
HEX = re.compile(r'0x([0-9a-f]+)')
COMMENT = re.compile(r'#\s*0x([0-9a-f]+)')
BRANCH = ('beq', 'bne', 'beqz', 'bnez', 'blt', 'bge', 'bltu', 'bgeu', 'bltz', 'bgez',
          'blez', 'bgtz', 'j', 'bgt', 'ble', 'bgtu', 'bleu')


def parse(path):
    out = []
    for ln in open(path, errors='replace'):
        m = LINE.match(ln.rstrip('\n'))
        if not m:
            continue
        mn = (m.group(3) or '').split('\t')[0].strip()
        if not mn:
            continue
        out.append({'pc': int(m.group(1), 16), 'mn': mn,
                    'ops': (m.group(4) or '').strip(),
                    'raw': ln.rstrip('\n').strip()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asm', required=True)
    ap.add_argument('--base', required=True)
    ap.add_argument('--code', type=lambda s: int(s, 0), required=True,
                    help='code size in bytes (.text.* from the linker map)')
    ap.add_argument('--payload', type=lambda s: int(s, 0), required=True,
                    help='total .payload size')
    ap.add_argument('--label', default='')
    a = ap.parse_args()

    base = int(a.base, 16)
    code_end = base + a.code
    pay_end = base + a.payload
    print(f'=== {a.label}')
    print(f'    entry {base:#x}  code end {code_end:#x}  payload end {pay_end:#x}')
    print(f'    (.text {a.code} bytes, rodata {a.payload - a.code} bytes)')

    ins = [i for i in parse(a.asm) if base <= i['pc'] < code_end]
    print(f'    {len(ins)} instructions in the code range')

    external, internal, paging, other = [], [], [], []
    for i in ins:
        pc, mn, ops = i['pc'], i['mn'], i['ops']
        if mn.startswith('0x'):          # undecodable word -> skip, not code
            continue
        if mn in BRANCH or mn == 'jal':
            m = HEX.search(ops)
            if not m:
                other.append((i, 'no target parsed'))
                continue
            t = int(m.group(1), 16)
            rec = (i, t)
            (internal if base <= t < pay_end else external).append(rec)
            continue
        if mn in ('auipc', 'lui'):
            cm = COMMENT.search(ops)
            if cm:
                t = int(cm.group(1), 16)
                rec = (i, t)
                (internal if base <= t < pay_end else external).append(rec)
            else:
                paging.append((i, None))
            continue
        if mn == 'jalr':
            other.append((i, 'jalr: ' + ('ret' if ops.replace(' ', '') in
                                         ('zero,0(ra)', 'ra') else ops)))
            continue
        if mn in ('call', 'tail'):
            m = HEX.search(ops)
            t = int(m.group(1), 16) if m else None
            (external if t is not None else other).append(((i, t), ''))
            continue
        # literal absolute address anywhere in the operands
        for m in HEX.finditer(ops):
            v = int(m.group(1), 16)
            if 0x40000000 <= v < 0x40100000 and not mn in ('auipc', 'lui'):
                rec = (i, v)
                (internal if base <= v < pay_end else external).append(rec)

    def dump(name, rows, verdict):
        print(f'\n  {name}: {len(rows)}')
        for i, t in rows:
            print(f'    {i["raw"]:52s} -> {t:#x} {verdict}' if t is not None
                  else f'    {i["raw"]:52s} -> ?')
        if not rows:
            print('    (none)')

    dump('EXTERNAL / absolute references (must exist in target firmware)', external, 'EXTERNAL')
    dump('INTERNAL references (body-relative, re-link fixes them)', internal, 'INTERNAL')

    print(f'\n  auipc/lui without a resolved comment: {len(paging)}')
    for i, _ in paging:
        print('    ' + i['raw'])
    print(f'\n  jalr / other control transfers: {len(other)}')
    for i, note in other:
        print(f'    {i["raw"]:52s} {note}')

    if external:
        print('\n  VERDICT: NOT portable as built.')
    else:
        print('\n  VERDICT: no external references. Every reference is body-relative.')


if __name__ == '__main__':
    main()
