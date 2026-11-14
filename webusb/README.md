# WebUSB pages — read the OS partition, and write a patched one

Two browser pages with deliberately opposite risk. Read the first, understand the second.

| Page | Writes? | What it does |
|---|---|---|
| `read-backup.html` | **No** | Reads the whole 868,352-byte OS partition over raw DFU and saves it with a sha256 receipt. |
| `flash.html` | **Yes** | Erases the 64 KiB sectors that differ and writes a patched partition, with a byte-exact preflight and verify. |

Both need desktop Chrome or Edge. WebUSB does not exist in Safari or Firefox.

---

## Why a local server

WebUSB and Web MIDI both require a **secure context**. `127.0.0.1` and `localhost` qualify;
`file://` does not. So:

```powershell
./serve.ps1
```

Then open `http://127.0.0.1:8000/read-backup.html`. That is the whole setup — no build step, no
dependencies, no `package.json`.

Before either page: **quit ToneCommand** (it holds the MIDI port and the USB device), unplug any
audio or headphone cable, keep the USB-C cable in, and connect one pedal only.

`dfu.js` is vendored and unmodified. All USB work goes through it.

---

## Page 1 — `read-backup.html` (no writes)

Its only data path is a DFU upload read. There is no erase, no image download and no
manifestation request anywhere in the page, and `test_read.js` checks that the block sequence is
exactly 212 reads of 4,096 bytes at the expected addresses.

1. Plug the pedal in normally. It enumerates as `33c3:1301`.
2. Click **enter update mode**. The page sends `F0 7D 44 46 55 01 F7` (normal MIDI, not DFU),
   waits 1,200 ms, and closes the port. The pedal re-enumerates as `33c3:6678` — "Lupgrader".
3. Click **connect** and pick that device.
4. Click **read**. It walks all 212 blocks and assembles the image.

You get `nanocore-os-readback.bin` (868,352 bytes) and a `.receipt.json` with `size`, `sha256`,
`readAtUtc`, `userAgent`, `osSize` and `blockSize`. There are two extra "save again" buttons
because browsers sometimes block a second programmatic download silently.

**There is no way out of update mode from software.** The loader has no exit command, so unplug
the pedal, wait a few seconds, and plug it back in. It returns to the application image. This is
not a limitation to work around — it is what makes a failed write recoverable.

---

## Page 2 — `flash.html` (writes)

Three stages, and nothing happens by accident.

### Stage 0 — plan only

Select three files from disk: your **stock readback**, the **patched image**, and the **patch
descriptor**. The page fetches nothing from the server. It then prints the length and sha256 of
each, the installed FIT hash, the descriptor hash, every check it ran, and the exact 64 KiB
erase plan. **No `navigator.usb` object is constructed in this stage.** If anything is wrong,
you have written nothing and you are still on stock firmware.

It checks, before touching the device:

- both images are exactly 868,352 bytes;
- `sha256(target)` and `sha256(target[0,759808))` match the pinned build constants;
- `sha256(expectedCurrent)` matches the pinned stock hash;
- the descriptor's own hash matches;
- `validateImage()` classifies the target as `installed` and your readback as `stock`;
- the planned sector set is exactly the pinned set, and the final partial sector
  `851,968..868,352` is **not** in it.

### Stage 1 — enter update mode

The same SysEx handshake as the read page. The pedal disappears from ToneCommand and comes back
as `33c3:6678`.

### Stage 2 — flash

The button stays disabled until Stage 0 passes **and** you type the exact confirmation phrase.
Every assertion is then repeated against the actual buffers immediately before the first USB
call, so a stale page, a swapped file or a mutated buffer cannot slip through.

`RawOS.program()` in `dfu.js` does the rest:

1. both `expectedCurrent` and `target` must be `Uint8Array` of exactly 868,352 bytes;
2. it **throws before any USB traffic** if a planned sector would cross `851,968` — the final
   partial sector holds device and preset data and can never be erased. `test_flash.js` proves
   this with a target tampered inside that region: rejection with zero transfers attempted;
3. it reads all 212 blocks of the live partition and compares **byte for byte** against
   `expectedCurrent`, aborting on a single difference — a pedal that is not exactly stock, or a
   half-written one, cannot be flashed again by accident;
4. it requires a `backupSaved` callback to return literally `true`. The page uses that to save a
   byte-identical copy of the live partition and a receipt before anything is erased;
5. it erases only the differing sectors, then writes 16 blocks per sector;
6. it re-reads all 212 blocks and compares **byte for byte** against `target`. On mismatch it
   stops with "Full readback mismatch; do not reboot; restore required";
7. only then does it manifest (download block 2, `GETSTATUS` status byte 8);
8. `transport.close()` deliberately never resets the device.

One attempt only, no retry loop.

> **`program()` authenticates nothing about `target`.** It does not know or care which image you
> gave it. The `BUILD` constants in `flash.js` and `validateImage()` are the only things standing
> between a wrong image and an erased partition. Do not replace them with a size check.

---

## The page is pinned to a build — repinning

`BUILD` in `flash.js` is a frozen object holding the build identity:

```js
targetSha256, targetFitSha256, stockSha256, descriptorSha256, expectedSectors, confirmation
```

Out of the box it is pinned to the **firmware 1.2 UniVibe** build, whose values are:

```
targetSha256      b374b5e364c73100f8678b61edda7916a1c62c7e2320d8083d098c72a681e222
targetFitSha256   af3b3d5c83a0213138aed66bdb1f1685afa2ed4bd94a250ce852e5985875d309
stockSha256       8ed563f16242610d634c6659cb0002b913a6a8ff89a1ffc9b0a2d4e913a684da
descriptorSha256  409e85e8a47e68d274db21b3f4dc233483a39f03c5a911a1894cf82b8e115968
expectedSectors   [0x000000, 0x040000, 0x080000, 0x0a0000]
confirmation      FLASH UNIVIBE 1.2
```

If you build your own patch, those values will not match yours and Stage 0 will refuse — which is
the correct behaviour. To repin, compute the values from **your** files:

```bash
node hash-files.js --target ./nanocore-patched-os.bin \
                   --stock  ./nanocore-os-readback.bin \
                   --descriptor ./patch.json
```

It prints the four hashes, the sector array, the per-sector changed-byte count, and warns if any
changed byte falls outside the erase plan. Paste the values into `BUILD`, change `effect` and
`confirmation` to something you have to think about, then:

```bash
node test_flash.js --stock ./nanocore-os-readback.bin \
                   --target ./nanocore-patched-os.bin \
                   --patch ./patch.json
```

That runs 23 assertions against the real files with a stub USB transport: image authentication,
the exact erase plan, the final-partial-sector exclusion, and the tamper rejection.

**No firmware images ship with this repo.** Both the test and the page expect files you produce
from your own readback.

---

## Files

| File | Purpose |
|---|---|
| `read-backup.html` / `.js` | Read-only partition read. |
| `flash.html` / `flash.js` | Write path: plan, stage, flash. |
| `dfu.js` | Raw DFU transport. Unmodified vendored copy — see below. |
| `firmware-lite.js` | Descriptor validator. Vendored with one line changed — see below. |
| `hash-files.js` | Computes the `BUILD` values for your own build. |
| `test_read.js` | 212-read sequence, offsets, block size, progress. |
| `test_flash.js` | 23 assertions over the real files, stub transport. |
| `serve.ps1` | Local HTTP server on port 8000. |

---

## Provenance of the two vendored files

`dfu.js` is byte-identical to `re-nanocore/web/dfu.js`:

```
sha256 281f2858a6be1e38de1c2843957a8c2aac881639b52b9f4f12613733cc03e2a5   10,216 bytes
```

Check it against your own checkout:

```powershell
Get-FileHash path\to\re-nanocore\web\dfu.js -Algorithm SHA256
```

`firmware-lite.js` is a copy of `re-nanocore/web/firmware.js` with **exactly one line changed**:
`'1.2'` added to the firmware-version whitelist in `schema()`.

```
original  0cd263b0ae62191f629401ba707ccf4f09dc85f089d4131d71a9da817aaedc7e   10,354 bytes
modified  76f2e288b8c08145a33a18998c8518801ffee17915d2b55a929a263ef5c100c3   10,360 bytes
```

The shipped whitelist lists `1.10`, `1.11` and `1.12`, so it rejects a valid 1.2 manifest with
"Unsupported patch manifest." That is a policy string, not an integrity check — schema version,
normalised-prefix hashing and the per-segment sha256 identity are byte-identical to upstream.

It is a local deviation, it is recorded in `../NOTICE.md`, and it is the only one.

---

## The write map, as an example

The 1.2 UniVibe patch changes 1,577 bytes across 14 ranges, which collapses into four 64 KiB
sectors:

| sector | changed | what |
|---|---|---|
| `0x000000` | 20 | FIT header: segment 3 crc32 and md5 recomputed |
| `0x040000` | 67 | MOD subtype-10 value formatter (68-byte region) |
| `0x080000` | 1458 | parameter defaults writer + the effect's DSP body |
| `0x0a0000` | 32 | effect name + two knob label rows |

262,144 bytes erased, 1,577 changed. The final partial sector `0xD0000..0xD4000` is never in the
plan, and the two images are byte-identical there.

Note that the FIT header is inside sector 0 and gets erased along with it. That is normal for
this class of patch: the header is a checksum table over the segments, so it must be rewritten
after the payload changes. Only the last segment's two hashes actually differ; the other six
fields are rewritten with identical values because the header is regenerated from the finished
image rather than patched field by field.

---

## If a write fails

An interrupted write does not destroy the pedal. It stays in the update-mode bootloader
(`33c3:6678`), which is reachable, and you can retry or restore.

- **Do not blindly retry the flash.** `program()` refuses unless the live partition is exactly
  stock, so a half-written pedal needs the restore path, not another attempt.
- **Restore:** re-read the partition with `read-backup.html` to see what is actually there, then
  write your saved stock image using the same page after repinning `BUILD` to it (target = your
  stock readback, so every sector differs and the whole image is rewritten).
- **Alternative:** the vendor updater installs an official `.fmu` over the device in that state.

Keep your readback. It is your only rollback.
