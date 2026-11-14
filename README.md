# nanocore-toolkit

Tools and reverse-engineered formats for the **Livtra Nanocore** guitar multi-effects pedal.

Produced while replacing all 30 AMP slots with NAM A2 Lite captures, and while porting a
custom MOD effect (UniVibe) to **firmware 1.2** — a build no public project covers. Everything
here was measured on hardware, not guessed from disassembly alone.

---

## What's here

| Path | What it is |
|---|---|
| `cli/` | `nanocore_cli.py` — convert `.nam` → pedal blob, upload / verify over USB MIDI, and validate custom-FX patch descriptors |
| `webusb/` | Two browser pages: read your OS partition over raw DFU (**read-only**), and flash a patched image with a byte-exact preflight |
| `fx12/` | The firmware 1.2 UniVibe patch — effect source, linker scripts, builder, audit tools, example descriptor |
| `docs/` | The formats: `NA2L.md`, `sysex.md`, `firmware-12.md` |

---

## What is new here

The pedal already has good public documentation. This repo exists for the parts that don't:

| Finding | Status elsewhere |
|---|---|
| **`NA2L` blob format** — 32-byte header, 1,871 float32 weights, 7,516 bytes total | Not documented. The only public mentions are a one-line aside in `nanocore-controller` and a resource listing in `re-nanocore`. No magic, header, struct, weight count or checksum anywhere. Public tooling routes through `.ead`/`SAPF` or `.nam` JSON instead. |
| **The pedal only accepts a two-submodel A2 container** | Unknown. Zero hits for `submodel`, `Lite` or `max_value` across all three public repos. A single-submodel `.nam` is rejected by the pedal's parser. |
| **Slot-info is 31 bytes, not 30** | All public parsers assume 30. `nanocore-controller` hard-rejects any other length; `livtra-nanocore-editor` silently drops the extra byte. It carries an `asset_type` field (`1` = DDPB, `3` = NA2L) that public code never sees. |
| **Firmware 1.2 patch sites and build** | Not documented. Public custom-FX work targets 1.10–1.12; every "1.2" hit in those repos is ToneCommand's version number or a WCAG rule. 1.2 is an *earlier* build, not shorthand for 1.12. |
| **A raw-DFU custom effect deployed on 1.2** | Public hardware runs are all on 1.10. This is the first write of this class on 1.2, and it changes the DSP body, the parameter defaults writer, the MOD value formatter, the effect name and two label tables. |

If you only read one file, read [`docs/NA2L.md`](docs/NA2L.md) — that is the format the pedal
actually stores, and it is the reason none of the existing converters produce something the
pedal will accept.

---

## Quick start — amp models

The pedal stores a flat blob, not `.nam` JSON. The CLI does the conversion and refuses
anything the pedal would reject.

```bash
pip install mido python-rtmidi
```

```bash
# What is on the pedal now?
python cli/nanocore_cli.py info

# Convert one capture (must be a two-submodel A2 Lite container)
python cli/nanocore_cli.py convert "My Amp.nam" -o out.na2l

# Convert a whole folder, then write slots 0..29 and verify every one
python cli/nanocore_cli.py convert-dir ./models -o ./na2l
python cli/nanocore_cli.py upload-dir ./na2l --start 0
python cli/nanocore_cli.py verify ./na2l

# Before you overwrite anything, dump what is already there
python cli/nanocore_cli.py dump ./slots-before
```

`upload-dir` writes the blob, sets the 16-character display name, then reads the slot back and
checks size and CRC. `verify` re-reads every slot and compares against the local files.

Slot indices are accepted as `0..29` even though the storage block reports 38 — the remaining
eight belong to a different asset class, so a loop over `storage.slot_count` will walk off the
end of the AMP region.

---

## Quick start — reading your own firmware

Everything here works from **your own** readback. No firmware image is distributed.

```powershell
cd webusb
./serve.ps1                     # http://127.0.0.1:8000
```

Open `read-backup.html` in desktop Chrome or Edge. This page has exactly one data path — a DFU
upload read — and never issues an erase, download or manifestation request. It also offers
the update-mode entry you need first (`F0 7D 44 46 55 01 F7`, then the pedal re-enumerates as
`33c3:6678`).

You get a 868,352-byte image and a receipt with its sha256. Keep it: it is your only rollback,
and the flash page will refuse to write anything if the live partition doesn't match the
expected stock image byte for byte.

Quit ToneCommand first — it holds the MIDI port.

---

## Custom effects

`webusb/flash.html` writes a patched OS partition, and `fx12/` builds one.

The write path is deliberately paranoid, because it has to be:

1. both `expectedCurrent` and `target` must be exactly 868,352 bytes;
2. the page checks three hard-coded sha256 values and requires the descriptor to classify the
   target as `installed` and your readback as `stock` — `program()` itself authenticates
   nothing about where the image came from;
3. it reads all 212 blocks of the live partition and compares **byte for byte** against
   `expectedCurrent`, aborting on a single difference;
4. it erases only the 64 KiB sectors that actually differ, and **refuses before any USB traffic**
   if a sector would cross `851,968` — the final partial sector holds device and preset data
   and is never touched;
5. after writing it re-reads all 212 blocks and compares against `target`; on mismatch it
   stops without manifesting;
6. it only then manifests. A failed flash leaves you in the update-mode bootloader, which is
   reachable — that is the whole reason the transport closes without resetting the device.

For the 1.2 UniVibe patch that is 4 sectors and 1,577 changed bytes:

| sector | changed bytes | what |
|---|---|---|
| `0x000000` | 20 | FIT header: seg3 crc32 and md5 recomputed |
| `0x040000` | 67 | MOD value formatter (68 B region) |
| `0x080000` | 1458 | parameter defaults writer + Velvet Vibrato DSP body |
| `0x0a0000` | 32 | effect name + two knob label rows |

`fx12/README.md` covers the build; `docs/firmware-12.md` has the addresses, the segment table,
the hash fields and the derivation behind each site.

---

## Requirements

| Piece | Needs |
|---|---|
| `cli/` | Python 3.9+, `mido`, `python-rtmidi` |
| `webusb/` | Desktop Chrome or Edge (WebUSB is not in Safari or Firefox), Node for the test harness |
| `fx12/` | Xuantie-900 `riscv64-unknown-elf` GCC 10.2.0 / binutils 2.35, `riscv64-unknown-elf-objcopy` |

`webusb/` runs from localhost only. WebUSB and Web MIDI both require a secure context, so
`file://` will not work — `serve.ps1` exists for that reason.

---

## Safety

- **Back up before writing anything.** The read page never writes, and the flash page refuses
  to proceed unless you have a byte-identical copy of the live partition.
- **Quit ToneCommand** before opening either page. It holds the MIDI port and the USB device.
- **One pedal, USB only, no hub, mains power.** The pedal is USB-powered; the cable staying in
  is the entire mitigation for a power loss mid-write.
- **Custom effects are unsupported by the vendor.** A bad image can leave the pedal on the
  bootloader. Recovery is a full-partition write of your own readback, or the official `.fmu`
  through the vendor updater.
- **`firmware-lite.js` is a local deviation**: one line, adding `'1.2'` to a version whitelist
  that otherwise rejects a perfectly valid manifest. It is a policy string, not an integrity
  check, and the change is documented in `webusb/README.md`.

---

## What is not in this repo

No firmware images, no OS readbacks, no patched partitions, no NAM models, no impulse
responses, and no vendor code. Those are either the vendor's or licensed to individuals, and
`re-nanocore`'s own scope document explicitly excludes them from its MIT grant. This repo ships
the *tooling* so you can do the same work with your own files.

See [`NOTICE.md`](NOTICE.md).

---

## Credits

Built on the work of three excellent public projects. All are MIT-licensed; GPLv3 is
one-way compatible with MIT, and their notices are retained in `NOTICE.md`.

- **[re-nanocore](https://github.com/jcuhnoio/re-nanocore)** (`jcuhnoio`) — the deepest public
  reverse-engineering of this pedal: firmware decoding, the FIT container, the FAL partition
  map, the DSP architecture, and the UniVibe effect this patch installs. Its `web/dfu.js` is
  vendored here unmodified, and `web/firmware.js` is the base for `firmware-lite.js`.
  `docs/firmware-12.md` would not exist without its 1.10–1.12 analysis to diff against.
- **[nanocore-controller](https://github.com/dvilelaf/nanocore-controller)** (`dvilelaf`) — the
  most complete AMP/IR opcode table, and the reference for slot semantics and the storage
  report. Its observation that `0x30` returns 38 slots of 12,288 bytes is what made the
  per-slot capacity, rather than a total, obvious.
- **[livtra-nanocore-editor](https://github.com/lucanenni/livtra-nanocore-editor)** (`Luca Nenni`) —
  a 93 KB MIDI mapping log from the browser editor's author, including the bit-plane packing
  scheme and the observed 64-byte chunk size.

Also: **[Neural Amp Modeler](https://github.com/sdatkinson/NeuralAmpModelerCore)** for the `.nam`
container format, and the **Xuantie-900** RISC-V toolchain that compiles the effect code.

---

## License

GNU General Public License v3.0 — see [`LICENSE`](LICENSE).

Copyright (C) 2026 soficis.

The GPL grant covers original work only. Vendor firmware, bootloaders, application binaries,
model weights, impulse responses and UI assets are not distributed here and are not covered.
Third-party MIT components retain their own notices — read [`NOTICE.md`](NOTICE.md) before
redistributing.
