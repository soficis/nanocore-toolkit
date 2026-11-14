# `fx12/` — the firmware 1.2 UniVibe patch

The build and audit tooling behind the first custom effect deployed on **firmware 1.2** of the
Livtra Nanocore.

> **1.2 is not 1.12.** It is an *earlier* build. The public projects cover 1.10, 1.11 and 1.12;
> 1.2 appears nowhere. Do not assume a 1.12 address works on 1.2 — see
> [`../docs/firmware-12.md`](../docs/firmware-12.md) for how each site was derived.

**Nothing here is a firmware image.** No vendor code, no `.fmu`, no OS partition. The scripts
read a stock readback *you* produce and build a patched image locally.

---

## Prerequisites

- Python 3.9+.
- A stock 868,352-byte OS readback from your own pedal — see [`../webusb/`](../webusb/).
- A `re-nanocore` checkout for the FIT decoder, descriptor schema and patch-application code:

  ```bash
  git clone https://github.com/jcuhnoio/re-nanocore.git
  ```

- For rebuilding effect code: the **Xuantie-900** RISC-V toolchain
  (`riscv64-unknown-elf-gcc` 10.2.0, binutils 2.35), expected under
  `~/nanocore_tools/xuantie/`, or point `--tool-prefix` at your own. The Docker route is not
  used here.

### Expected layout and overrides

Scripts resolve paths from the environment, so nothing is tied to one machine:

| variable | meaning | default |
|---|---|---|
| `NANOCORE_REPO` | your `re-nanocore` checkout | `~/nanocore_tools/repos/re-nanocore` |
| `NANOCORE_WORK` | scratch workspace (holds `build_out/`, `ui12/`) | current directory |
| `NANOCORE_READBACK` | your stock 868,352-byte image | `$NANOCORE_WORK/nanocore-os-readback.bin` |

`~` is expanded. Relative paths work.

---

## Files

| File | Purpose |
|---|---|
| `build_xuantie.py` | Compiles and links the effect body against a chosen firmware's site table. |
| `vibe_format_12.S` + `vibe_format_12.ld` | The 68-byte MOD subtype-10 value formatter, relocated for 1.2. |
| `vibe_defaults_12.S` + `ui_12.ld` | The 128-byte parameter defaults writer. |
| `build_patch_12.py` | Applies the six site patches, regenerates the FIT header, writes the descriptor. |
| `audit_patch_12.py` | Checks descriptor self-consistency: lengths, `beforeHex` against the image, region alignment. |
| `audit_refs.py` | Proves a payload has no external references (`jal`, `jalr`, `auipc`). |
| `diff_report_12.py` | Full before/after diff, collapsed into maximal ranges, and asserts nothing outside them changed. |
| `verify_vs_shipped.py` | Compares a freshly built payload against the author's published hashes at a known site. |
| `build_plan_12.py` | Converts the descriptor into re-nanocore's `raw-os-fit` plan for `raw_dfu.py`. |
| `examples/patch-1.2.json` | The finished descriptor — the exact patch that was flashed. |

---

## Build order

### 1. Effect body

```bash
python build_xuantie.py --version 1.12 --address 0x400738ea --effect univibe --output build-1.2
```

`SITES` in `build_xuantie.py` has entries for `1.10`, `1.11` and `1.12` only. There is no `'1.2'`
entry **because none is needed**: the code generation is identical and only the link address
differs. That is safe here for a specific, checked reason — the UniVibe payload has **zero
external references** (no `jal`, no `jalr`, no `auipc`; every branch internal), so it does not
care where it lives. Comparing the 1.12 link against the 1.2 link, exactly 43 instructions
differ and all 43 are page materialisation.

If you add a `'1.2'` entry, do it by adding the address to `SITES`, then re-run
`verify_vs_shipped.py` to confirm the blobs still reproduce.

`--force` is required if the output directory already exists.

### 2. The two UI blobs

`vibe_format_12.S` and `vibe_defaults_12.S` are assembled and linked with their own linker
scripts. The linked outputs must be exactly **68** and **128** bytes — those sizes are why the
patch fits: each replaces a region of identical length in the image.

### 3. Audit the payload before you trust it

```bash
python audit_refs.py --asm build-1.2/univibe.dis --base 0x400738ea --code 0x400738ea \
                     --payload 0x400738ea --label univibe
```

See `audit_refs.py` for the exact argument semantics. A non-zero count of external references
means the blob **cannot** be relocated and the address you chose is wrong.

### 4. Build the descriptor

```bash
python build_patch_12.py
```

It reads the stock readback, applies the six patches, recomputes the segment hashes, and writes
`patch-1.2.json` plus a report. It also cross-checks every `beforeHex` against the image and
refuses if a site does not contain what it expects.

### 5. Two independent seals on the result

```bash
python audit_patch_12.py      # descriptor self-consistency
python diff_report_12.py      # 14 ranges, 1,577 bytes, nothing outside them
```

`diff_report_12.py` is the one to read before flashing. It prints every maximal run of differing
bytes and asserts that all of them fall inside a declared range. For the shipped patch: 14
ranges, 1,577 differing bytes, 50 maximal runs, **zero** outside.

---

## What the descriptor looks like

```jsonc
{
  "schemaVersion": 1,
  "firmwareVersion": "1.2",
  "imageVersion": "1.2",
  "effect": "UNIVIBE",
  "replaces": "VELVET VIBRATO",
  "osSize": 868352,
  "fitSize": 759808,
  "stockFitSha256": "c45b9d87…b2a954a",
  "installedFitSha256": "af3b3d5c…75d309",
  "normalizedFitSha256": "930a672c…605fafb",
  "timestamp": { … },
  "patches": [ { "offset": 569578, "beforeHex": "…", "afterHex": "…" }, … ],  // 6
  "hashes":  [ { "offset": …, "segment": …, "algorithm": "crc32|md5",
                 "beforeHex": "…", "afterHex": "…" }, … ],                   // 8
  "segments": [ … ]                                                             // 4
}
```

Offsets are decimal integers, not strings. `beforeHex` is verified against your image before
anything is written; a mismatch is the difference between "this descriptor is for your firmware"
and "this descriptor is for someone else's".

`hashes` covers eight FIT header fields. Six of them are rewritten with **identical** values —
the header is regenerated from the finished image, not patched field by field — and only the
last segment's crc32 and md5 actually change. That is expected, not a bug, and the bootloader
enforces all eight.

### The six sites

| offset | vaddr | size | what |
|---|---|---|---|
| `0x08b0ea` | `0x400738ea` | 1366 B | Velvet Vibrato DSP body → UniVibe |
| `0x08b06a` | `0x4007386a` | 128 B | parameter defaults writer |
| `0x044996` | `0x4002d196` | 68 B | MOD subtype-10 value formatter |
| `0x0a1a7c` | `0x4008a27c` | 15 B | display name (`VELVET VIBRATO` → `UNIVIBE`) |
| `0x0a2c6c` | `0x4008b46c` | 16 B | uppercase knob labels |
| `0x0a297c` | `0x4008b17c` | 16 B | titlecase knob labels |

---

## Why you can believe the addresses: they were reproduced, not guessed

The strongest evidence in this directory is negative: the same source, compiled for the
**1.12** address, reproduces the author's shipped `patch-1.12.json` body with **0 differing
bytes**, and the **1.10** address reproduces `patch.json` exactly. The `gain` effect matches the
author's recorded hashes at both sites too.

That proves the compiler, flags and link options are faithful. It does not prove the 1.2
addresses — those come from static analysis of the 1.2 image itself (see
`../docs/firmware-12.md`), plus the fact that the payload cannot reference anything outside
itself.

One site was derived wrongly first. The initial descriptor pointed patch 3 at `0x4002beaa`, which
verification showed to be the Synth Mode voice-name display, not the Velvet formatter. It was
discarded and re-derived to `0x4002d196`, a 68-byte index tail matching 1.12's structure. It is
documented rather than quietly fixed because a plausible-but-wrong site is the failure mode most
likely to still be hiding somewhere.

---

## Safety

- **`build_patch_12.py` produces an image; it does not write one.** Flashing is
  `../webusb/flash.html`, which compares your live partition byte for byte before erasing
  anything.
- `program()` in the DFU layer **performs no descriptor validation**. `build_plan_12.py` exists
  to feed re-nanocore's `raw_dfu.py`, whose deploy path is protocol-tested but was not
  hardware-exercised by its author. This project flashed with the WebUSB page instead.
- Keep the stock readback. It is the only rollback.

## Known regression

**SPEED displays 0–100% instead of the stock exponential/Hz figure.** The index-0 formatter arm
was dropped to fit the 68-byte region. The stored parameter value and the DSP output are
unchanged — only the rendered number differs. This is accepted, not pending.

Unmeasured: whether the defaults carried over from the 1.12 build (0.35 / 0.8 / 0.5) suit 1.2
subjectively, and whether the MODE encoder's detents are genuinely two-state. The display flip is
proven; the feel is your ear's call.
