# `fx12/` — the firmware 1.2 UniVibe patch

The build and audit tooling behind the first custom effect deployed on **firmware 1.2** of the
Livtra Nanocore.

> **1.2 is not 1.12.** It is a *newer* branch, released on 11 Nov 2026; the version number sorts
> below 1.10, but the build follows 1.12. `re-nanocore` added 1.2 targets on 16 Nov 2026, so this
> is no longer the only 1.2 work. Do not assume a 1.12 address works on 1.2 — see
> [`../docs/firmware-12.md`](../docs/firmware-12.md) for how each site was derived, and
> [`../docs/firmware-12-images.md`](../docs/firmware-12-images.md) before flashing either image.

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
- For `deploy_12.py`: `mido` and `python-rtmidi` to request update mode, and `libusb-package`
  (or `--libusb`) for the raw-DFU write. `pip install mido python-rtmidi libusb-package`.

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
| `descriptor_12.py` | Shared schema-1/schema-2 load, classify and apply logic. |
| `build_from_descriptor.py` | Builds any 1.2 image from its descriptor and a stock readback. |
| `identify_image.py` | Says which descriptor, and which state, an OS readback is — offline. |
| `deploy_12.py` | Raw DFU: `enter`, `backup`, descriptor-authenticated `deploy`, and `restore`. |
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

`--version 1.12` in that command only satisfies the default selector; `--address`
overrides the address it picks. It is also copied into the receipt as
`firmware_version`, so today's 1.2 build records `\"1.12\"` there. Adding the
`'1.2'` entry fixes the receipt as well as the command line.

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

- **The builders produce an image; they do not write one.** The 13 Nov UniVibe build was
  flashed with `../webusb/flash.html`; the 18 Nov combined image went over raw DFU with
  `deploy_12.py`.
- `program()` in the DFU layer **performs no descriptor validation**. `deploy_12.py` therefore
  classifies the image against its descriptor before opening USB, requires the live partition
  hash to match `--expected-current`, and verifies the readback before manifesting.
  `webusb/flash.js` does the same job with four pinned hashes.
- Keep the stock readback. It is the only rollback — or `deploy_12.py restore` with a saved
  partition.

## Other 1.2 descriptors

`build_from_descriptor.py`, `identify_image.py` and `deploy_12.py` accept schema 1 and 2, so
re-nanocore's UniVibe and its combined Octaver build without editing addresses. Identify a
readback first:

```
python identify_image.py --image <readback> \
    --descriptor examples/patch-1.2.json --descriptor ../path/to/web/octave-1.2.json
```

It prints `stock` or `installed`, the effect and the installed FIT hash, and exits 0 for one
identity, 1 for none, 2 for more than one installed identity. Then build and deploy:

```
python build_from_descriptor.py --descriptor octave-1.2.json \
    --stock <stock-readback> --out octave-os.bin
python deploy_12.py enter
python deploy_12.py backup --out backup --libusb <dll>
python deploy_12.py deploy --descriptor octave-1.2.json --image octave-os.bin \
    --expected-current <sha256> --out deploy --libusb <dll>
python deploy_12.py restore --image backup/os-partition.bin \
    --expected-current <sha256> --out restore --libusb <dll> --confirm "RESTORE <sha8>"
```

`build_from_descriptor.py` refuses unless the input classifies as `stock` and the output as
`installed` for that descriptor. `deploy` refuses unless the image classifies as `installed`
and the live partition matches `--expected-current`; `restore` writes a saved partition back
and requires the confirmation phrase. The three published images and their hashes are in
[`../docs/firmware-12.md#working-with-any-12-descriptor`](../docs/firmware-12.md#working-with-any-12-descriptor).

Do not mix schemas: a schema-1 image will not classify under a schema-2 descriptor, and vice
versa. Classification, not the file name, is what the tools trust.

## Known regression

**SPEED displays 0–100% instead of the stock exponential/Hz figure.** The index-0 formatter arm
was dropped to fit the 68-byte region. The stored parameter value is unchanged, and no DSP code
was touched on this path — only the rendered number differs. This is accepted, not pending.

Unmeasured: whether the defaults carried over from the 1.12 build (0.35 / 0.8 / 0.5) suit 1.2
subjectively, and whether the MODE encoder's detents are genuinely two-state. The display flip
was observed on the pedal; the feel is your ear's call.
