# Custom effects on firmware 1.2

The pedal's firmware line runs **1.2, 1.10, 1.11, 1.12** — note that `1.2` is an
early build, not an abbreviation of `1.12`. Existing public work targets `1.10`,
`1.11` and `1.12`; at the time of writing, firmware `1.2` appears nowhere in any
of the three community repositories.

This directory is the missing case: the addresses, the patch descriptor, and a
build that was flashed to real hardware on 13 Nov 2026 and boots.

One consequence worth knowing before you write a validator: the 1.2 OS image's own
FIT header still reports `image_version` **`1.12`**. Firmware 1.2 is a distinct
build, but the version field inside its container was not moved down with it — so
anything that infers the firmware version from the FIT sees `1.12` on a `1.2`
pedal. The descriptor here states `firmwareVersion: "1.2"` explicitly rather than
inferring it.

What it does: replaces the DSP body of MOD slot 10, factory **Velvet Vibrato**,
with **UniVibe** (`re-nanocore`'s open-source chorus/vibrato), plus the UI needed
to drive it — display name, four knob labels, parameter defaults, and the value
formatter. Nothing else in the chain is touched.

---

## The six patch sites

All offsets are into the 868,352-byte OS partition as exposed over raw DFU.

| # | file offset | load address | size | what |
|---|---:|---:|---:|---|
| 0 | `0x044996` | `0x4002d196` | 68 | MOD subtype-10 value formatter (percentage, `CHORUS`/`VIBRATO`) |
| 1 | `0x08b06a` | `0x4007386a` | 128 | parameter defaults writer + `SPEED`/`MODE` labels |
| 2 | `0x08b0ea` | `0x400738ea` | 1366 | Velvet Vibrato DSP body → UniVibe payload |
| 3 | `0x0a1a7c` | `0x4008a27c` | 15 | effect name `VELVET VIBRATO` → `UNIVIBE` |
| 4 | `0x0a297c` | `0x4008b17c` | 16 | titlecase knob label row |
| 5 | `0x0a2c6c` | `0x4008b46c` | 16 | uppercase knob label row |

Plus the FIT segment hashes, below.

The DSP body sits at `0x400738ea` and the slot is 1,366 bytes, ending exactly at
`0x40073e40` — the start of the next function. That boundary is what makes the
size trustworthy rather than chosen.

## The FIT header

The OS partition begins with a FIT header describing four segments.

| segment | file offset | size | load address | crc32 | md5 |
|---|---:|---:|---|---|---|
| seg0 | 2,048 | 34,160 | `0x30040000` | `de5fadd6` | unchanged |
| seg1 | 36,864 | 6,240 | `0x30060000` | `53a638d3` | unchanged |
| seg2 | 45,056 | 50,220 | `0x30080000` | `70cabc94` | unchanged |
| seg3 | 96,256 | 663,416 | `0x40000000` | `164bca2b` → **`4de70cba`** | `2c59d5b3…4c1b` → **`22ddb1af…6afc`** |

Eight hash fields, at offsets `0x00016c`, `0x0001a0`, `0x000290`, `0x0002c4`,
`0x0003b4`, `0x0003e8`, `0x0004d8`, `0x00050c`. Six are rewritten with **identical
values** — that is deliberate. The writer recomputes all eight from the finished
image rather than editing seg3's pair in place, so the header is a product of the
image, not a patch on top of it.

**The bootloader does enforce these.** Recomputing seg3's crc32 and md5 was the
one genuinely unknown step: every prior hardware deployment was on 1.10, and this
pair had never been through a 1.2 bootloader. It was accepted.

## What actually differs

1,577 bytes across 14 ranges, and **0 bytes outside them**:

| offset | size | changed | region |
|---:|---:|---:|---|
| `0x00016c` | 4 | 0 | FIT hash seg0 crc32 |
| `0x0001a0` | 16 | 0 | FIT hash seg0 md5 |
| `0x000290` | 4 | 0 | FIT hash seg1 crc32 |
| `0x0002c4` | 16 | 0 | FIT hash seg1 md5 |
| `0x0003b4` | 4 | 0 | FIT hash seg2 crc32 |
| `0x0003e8` | 16 | 0 | FIT hash seg2 md5 |
| `0x0004d8` | 4 | 4 | FIT hash seg3 crc32 |
| `0x00050c` | 16 | 16 | FIT hash seg3 md5 |
| `0x044996` | 68 | 67 | MOD subtype-10 formatter |
| `0x08b06a` | 128 | 126 | defaults writer |
| `0x08b0ea` | 1366 | 1332 | Velvet DSP body |
| `0x0a1a7c` | 15 | 13 | effect name |
| `0x0a297c` | 16 | 10 | titlecase labels |
| `0x0a2c6c` | 16 | 9 | uppercase labels |

The ranges are padded to fixed sizes because the writer replaces whole blocks, and
the replacement is zero-filled past its own length. A "changed" count below the
range size means the tail already matched.

## Erase map

Flash is erased in 64 KiB sectors, so a 1,577-byte change costs 262,144 bytes of
erase. Only four sectors are involved:

| sector | changed bytes | contains |
|---|---:|---|
| `0x000000..0x00ffff` | 20 | FIT header |
| `0x040000..0x04ffff` | 67 | formatter |
| `0x080000..0x08ffff` | 1,458 | defaults + DSP body |
| `0x0a0000..0x0affff` | 32 | name + label rows |

The final partial sector `0xd0000..0xd4000` (851,968..868,352) holds device-side
data and preset state. It is never erased, and the write path refuses to run if a
planned sector would reach into it.

## Where the stock image came from

Not from the `.fmu` download. The full 868,352-byte OS partition was read off the
pedal over raw DFU first (sha256
`8ed563f16242610d634c6659cb0002b913a6a8ff89a1ffc9b0a2d4e913a684da`), and the
patched image was built from **that**.

The reason matters: the decoded `.fmu` covers only the first 759,808 bytes. The
remaining 108,544 bytes are device-side — 2,040 bytes of real data followed by
106,504 bytes of `0xFF`. Building from the readback carries that tail over
byte-for-byte instead of reconstructing it from an assumption.

Cross-check: the first 759,808 bytes of the readback are byte-identical to the
decoded official `nanocore-1.2.fmu` payload (sha256 `c45b9d87…b2a954a`).

## Building it yourself

You need:

1. **Your own readback** of the OS partition (`webusb/read-backup.html`).
2. The **Xuantie RISC-V toolchain** — `riscv64-unknown-elf-gcc` 10.2.0,
   binutils 2.35 (`Xuantie-900 elf newlib gcc Toolchain V2.6.1 B-20220906`).
3. The UniVibe source and the UI blobs.

Then:

```
python build_patch_12.py    # descriptor from your readback
python build_xuantie.py     # compile the DSP body at 0x400738ea
python audit_refs.py        # prove the body has no external references
python diff_report_12.py    # the 14 ranges, and nothing outside them
```

`build_patch_12.py` reads the image and derives the descriptor; it does not take
offsets on trust. `audit_refs.py` is the relocation check and the reason this is
safe at all — see below.

### Why relocating DSP code is safe here

The compiled UniVibe body has **zero external references**: no `jal`, no `jalr`,
no `auipc`. Every branch is internal; the only two `lui` values are its own rodata
page and a numeric tag constant. Compared against the same source linked for
firmware 1.12, exactly 43 instructions differ and all 43 are page materialisation.

A body that called into firmware helpers would break when moved to a different
address. This one cannot.

## How the 1.2 addresses were established

By disassembly of the decoded 1.2 image, not by pattern-matching 1.12:

- the MOD dispatcher at `0x4006b568` branches on subtype 10,
- builds the state pointer at `state + 0x142c8`,
- calls `0x400738ea` — the Velvet entry.

Cross-checked by content: the case at `0x4002cb58` contains a subtype-10 arm at
`0x4002d190` whose shape matches 1.12's equivalent.

**One wrong site was found and corrected during this work.** The first descriptor
pointed the formatter patch at `0x4002beaa`. That code turned out to be the Synth
Mode voice-name display — block index 10, reading a 10-entry voice table — not the
Velvet formatter. The patch was discarded and re-derived to `0x4002d196`, a
68-byte index tail matching 1.12's structure. Worth knowing about because a
plausible-but-wrong site is exactly the failure mode that survives review.

## Deliberate regression

The **SPEED knob reads `0..100%`** instead of the stock exponential/Hz number.

The 68-byte formatter region had to lose the index-0 `pow`/Hz arm to fit the
percentage renderer and the `CHORUS`/`VIBRATO` strings. The parameter value sent to
the DSP is unchanged — this is a display-only change. If you rebuild with a larger
slot available, you can restore it.

## Also worth knowing

- `program()` performs **no descriptor validation at all**. Any authentication of
  the image has to happen in the caller. `webusb/flash.js` therefore hard-codes
  the whole-partition sha256, the FIT-prefix sha256 and the stock sha256, and
  re-checks them on the buffers immediately before the first USB call.
- `re-nanocore`'s raw-DFU backend is, in its author's words, protocol-tested but
  not hardware-exercised. This deployment was its first physical write. It worked.
- Firmware `1.2` custom effects are **new**. If you are on 1.10–1.12, use
  `re-nanocore`'s own patch manifests instead — they are better tested.
