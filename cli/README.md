# `nanocore_cli.py` — host CLI

Everything over USB MIDI SysEx. No DFU, no libusb, no vendor software. The pedal appears as a
plain MIDI port named `Nanocore`.

```bash
pip install mido python-rtmidi
python nanocore_cli.py --help
```

`--port` goes **before** the subcommand and takes a substring match against the MIDI port
names. If omitted, the first port whose name contains `nanocore` (case-insensitive) is used,
falling back to the single port if only one exists.

> Quit ToneCommand first. It opens the same MIDI port and you will get silent writes or
> `port is busy`.

---

## Commands

| Command | Arguments | What it does |
|---|---|---|
| `info` | — | Storage report (`0x30`) and all 38 slot-info records (`0x36`). Read-only. |
| `dump` | `<output> [--start N] [--count N]` | Read slots out to `.bin` files. Read-only. |
| `convert` | `<nam> [-o out.na2l]` | One `.nam` → `.na2l` blob. |
| `convert-dir` | `<directory> -o <outdir>` | Every `.nam` in a folder. |
| `upload` | `<slot> <file> [--name NAME]` | Write one `.na2l` to one AMP slot. |
| `upload-dir` | `<directory> [--start N] [--names names.json]` | Write a folder of `.na2l` to consecutive slots. |
| `verify` | `<directory> [--start N]` | Read back and compare CRC + size against local files. |
| `effects list` | `[--repo PATH]` | The custom-effect catalogue in a re-nanocore checkout. |
| `effects prepare` | `--fmu F --patch P --output O [--repo PATH]` | Patch a decoded firmware image, offline. |
| `effects backup` | `<output>` | Dump every slot and write a hash manifest. |
| `effects deploy` | `--image I --backup-dir D --expect-sha256 H --current-sha256 C [--repo PATH] [--yes]` | Flash over raw DFU. See the warning below. |

Exit code `1` on a refused or failed operation, `0` on success, `2` for `effects deploy`
without `--yes`.

---

## Amp models

### `convert` / `convert-dir`

The pedal does not accept `.nam` JSON. It stores a flat `NA2L` blob, 7,516 bytes, described in
[`../docs/NA2L.md`](../docs/NA2L.md).

`convert` is strict on purpose. It refuses with:

```
not a two-submodel SlimmableContainer
first submodel is not max_value 0.5
first submodel has N weights, expected 1871
```

Those are not stylistic warnings — a single-submodel file, or a Lite model at a different
`max_value`, is silently wrong on the pedal.

### `upload` / `upload-dir`

Before writing, the CLI calls `info()` and cross-checks the *live* storage report: slot must be
below `slot_count`, and the blob must fit `max_bytes`. Then:

1. `0x31` `BEGIN` with `len | (slot << 24)`
2. `0x32` `CHUNK`, 64 bytes at a time, each with `offset | (slot << 24)`
3. `0x33` `COMMIT` with the size and a CRC-32 over the blob
4. `0x35` `NAME`, slot + up to 16 printable ASCII characters
5. `0x36` `SLOT_INFO`, to confirm the size and checksum that landed

If any step raises, it sends `0x34` `ABORT` rather than leaving a half-written slot.

`upload-dir` sorts the directory and walks slots from `--start`. `--names` takes a JSON object
keyed either by slot number or by filename stem, valued with the display name:

```json
{ "0": "TWIN CLEAN", "plexi_68": "PLEXY 68" }
```

Names are truncated to 16 characters, restricted to printable ASCII, and right-stripped.

### `verify`

Reads each slot in 200-byte windows (`0x39`, payload `<BHB` = slot, offset, length), checks the
final CRC against the stored checksum, and compares against the local file byte for byte.

`verify` takes the directory positionally, not `--dir`.

### `dump`

Writes each populated slot to `<slot>_<name>.bin`. Run this before `upload-dir` — the pedal
has no undo.

---

## Custom effects

`effects prepare` is the useful one and it runs entirely offline:

```bash
python nanocore_cli.py effects prepare \
  --fmu nanocore-1.2.fmu \
  --patch patch-1.2.json \
  --output nanocore-1.2-univibe-os.bin
```

It validates the descriptor against the decoded image, applies every patch, recomputes the FIT
segment hashes, and refuses with

```
Unknown firmware layout or build; nothing will be written.
```

if the descriptor's expected bytes do not match the firmware you supplied. That refusal is the
main safety property: a descriptor for a different build cannot produce a half-patched image.

It needs a `re-nanocore` checkout (`--repo`, or `~/nanocore_tools/repos/re-nanocore`, or
`./re-nanocore`) because the descriptor schema and image decoder come from there:

```bash
git clone https://github.com/jcuhnoio/re-nanocore.git
```

### `effects deploy` — read this first

This subcommand shells out to `re-nanocore/tools/custom_fx/raw_dfu.py`. It is included for
completeness, but **it is not the path this project used, and it is not validated on our
hardware.** It needs libusb and a working PyUSB backend, which does not exist on every
platform — on Windows the WebUSB pages in `../webusb/` are the working route, and that is what
actually flashed the pedal.

It also demands more than a slot backup: `--backup-dir` must contain a full 868,352-byte
`os-partition.bin`, because the underlying tool will not proceed without a restorable copy of
the whole OS partition, plus a `--current-sha256` of the live partition and `--yes`.

If you want a custom effect on the pedal, prefer `webusb/flash.html`: it byte-compares the live
partition against a known stock image before erasing anything, and it verifies the full
readback before manifesting.

---

## Why 38 slots, not 30

`0x30` reports 38 slots and a 12,288-byte limit. Those are not alternatives: it is
**38 slots of 12,288 bytes each** (466,944 bytes total, `38 × 0x3000 = 0x72000`). Only slots
0–29 are AMP slots; the rest belong to another asset class.

The CLI therefore accepts `--start` only in `0..29` for writes, and refuses a slot index at or
above `storage.slot_count` as reported by the live device. If you write your own tool, do not
loop `range(storage.slot_count)` and assume it is the amp region — it is not.

A NA2L blob is 7,516 bytes, comfortably inside the 12,288-byte slot.

---

## Type notes

- `debug` output for the SysEx layer is intentionally off; add `--port` and inspect with a MIDI
  monitor if a write misbehaves.
- All replies are matched on `(sequence, command)`; the pedal will not answer a message whose
  body version byte is not `2`.
- `info` requires a reply of at least 20 bytes; `slot_info` at least 30. The CLI tolerates a
  31-byte record and reads `asset_type` from byte 30 when present — public parsers do not, see
  [`../docs/sysex.md`](../docs/sysex.md).
