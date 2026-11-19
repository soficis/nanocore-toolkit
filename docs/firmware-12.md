# Custom effects on firmware 1.2

The pedal's firmware line runs **1.2, 1.10, 1.11, 1.12** — `1.2` is a newer
branch, not an abbreviation of `1.12`. It was released on 11 Nov 2026; the
version number sorts below `1.10`, but the build follows `1.12`. `re-nanocore`
added 1.2 targets on 16 Nov 2026, so this is no longer the only 1.2 work.

This directory documents the first public 1.2 custom effect: the addresses, the
patch descriptor, and a build flashed to real hardware on 13 Nov 2026 that
boots. It has not been played through a guitar and amp yet, so nothing here is
a claim about how it sounds. Two different 1.2 UniVibe images now exist — see
[`firmware-12-images.md`](firmware-12-images.md) before flashing either.

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
one genuinely unknown step: at the time, every public hardware deployment had been
on 1.10, and this pair had never been through a 1.2 bootloader. It was accepted,
and `re-nanocore`'s 1.2 images, which set `hardwareTested: true`, recompute the
same fields.

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

## Working with any 1.2 descriptor

`re-nanocore`'s Octaver is a different format: `schemaVersion` 2, 81 patches, a 55-byte
trampoline at the Velvet site and 37,952 bytes appended after the stock FIT. It replaces the
stock Octaver/PITCH slot as well as Velvet Vibrato, so it is not the six-site patch above.
Its normalized hash covers only the timestamp, not every patched range.

Four tools handle both schemas, so the other UniVibe is not a special case:

| tool | does |
|---|---|
| `fx12/build_from_descriptor.py` | descriptor + stock readback → image; refuses unless the input classifies as stock and the output as installed |
| `fx12/identify_image.py` | reports which descriptor and which state an OS readback is, offline |
| `fx12/deploy_12.py enter` | normal mode → Lupgrader over MIDI SysEx |
| `fx12/deploy_12.py backup` / `deploy` / `restore` | raw DFU read, authenticated write, saved-partition write-back |

Build and identify first; they touch no USB:

```
python fx12/build_from_descriptor.py --descriptor octave-1.2.json \
    --stock <readback> --out octave-os.bin
python fx12/identify_image.py --image octave-os.bin \
    --descriptor fx12/examples/patch-1.2.json --descriptor octave-1.2.json
```

The second command should report `installed UNIVIBE_OCTAVE`, not `stock`.

Flashing, with the pedal in normal mode, ToneCommand closed and audio cables out:

```
python fx12/deploy_12.py enter
python fx12/deploy_12.py backup --out backup --libusb <dll>       # prints the live sha256
python fx12/deploy_12.py deploy --descriptor octave-1.2.json \
    --image octave-os.bin --expected-current <sha256> --out deploy --libusb <dll>
```

`deploy` classifies the image against the descriptor before opening USB, re-reads the
partition, refuses unless the live hash matches, writes only changed 64 KiB sectors and
requires a byte-exact readback before manifesting. `restore` writes a saved partition back
and requires `--confirm "RESTORE <first 8 hex>"`; that is the path back to stock, or to the
image you were running before.

Flashed here on 18 Nov 2026. The preflight readback was the 13 Nov UniVibe image
(`b374b5e3…a681e222`); the post-write readback matched the target exactly
(`7c2656e9…72284511`); the pedal re-enumerated as `33c3:1301` afterwards. The documented
`deploy_12.py` command was then run again against that same live partition: it classified the
image, read the partition, erased **zero** sectors, and matched the readback exactly
(`manifestation_status` `000000000800`). It has **not** been played, and the descriptor still
sets `workInProgress: true`.

### Test vector

From the stock readback `8ed563f1…13a684da`, all three published 1.2 UniVibe images rebuild
byte for byte:

| descriptor | full image sha256 | installed FIT sha256 | changed 4 KiB blocks |
|---|---|---|---|
| this repo, v1 | `b374b5e3…a681e222` | `af3b3d5c…875d309` | 5 |
| re-nanocore v1 | `2be1b263…a580059c` | `fac0d102…ebce920` | 7 |
| re-nanocore v2 | `7c2656e9…72284511` | `42411bad…f0cd9e54` | 26 |

The full-image hashes are timestamp-dependent: a build preserves the timestamp in the
readback it was given. Compare the installed FIT hash, or run `identify_image.py`; do not
compare a full-image hash from a different readback.

## Also worth knowing

- `program()` performs **no descriptor validation at all**. Any authentication of
  the image has to happen in the caller. `webusb/flash.js` therefore hard-codes
  the whole-partition sha256, the FIT-prefix sha256 and the stock sha256, and
  re-checks them on the buffers immediately before the first USB call.
- `re-nanocore`'s raw-DFU backend was, in its author's words, protocol-tested but
  not hardware-exercised when this build used it. This deployment was its first
  physical write. It booted, and `re-nanocore` has since published 1.2 images
  that set `hardwareTested: true`.
- If you are on 1.10–1.12, use `re-nanocore`'s own patch manifests; they are older
  and better tested. On 1.2 there are now two independent UniVibe images — see
  [`firmware-12-images.md`](firmware-12-images.md) before choosing one. Neither
  has been auditioned.
