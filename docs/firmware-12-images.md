# Firmware 1.2 images: two UniVibe builds and how to tell them apart

Firmware 1.2 is no longer a single artifact. Two independent UniVibe patches
target the same stock release, built from different code and with different
patch maps. This repo's image has been flashed and boots, but has not been
played; `re-nanocore` sets `hardwareTested: true` on both of its images (v2 is
still marked work-in-progress), which by its own notes means flashed, not
auditioned. They are not interchangeable, and a tool that treats "1.2 UniVibe"
as one image will eventually flash the wrong bytes.

This note records the shared anchors, the differences that matter, and the
rules for handling a 1.2 descriptor.

## The stock side is identical

Both projects start from the same official release:

| what | value |
|---|---|
| release date | 2026-11-11 |
| official package sha256 | `0bf64603…4d2b103d` |
| OS partition | 868,352 bytes |
| stock FIT | 759,808 bytes, sha256 `c45b9d87…b2a954a` |
| stock OS readback used here | sha256 `8ed563f1…13a684da` |

The eight FIT hash fields and the four segment hashes of the stock image are
the same in both descriptors. Any disagreement there means the input is not the
stock 1.2 image.

## Two v1 UniVibe descriptors

| | this repo | re-nanocore |
|---|---|---|
| descriptor | `fx12/examples/patch-1.2.json` | `web/patch-1.2.json` |
| descriptor sha256 | `409e85e8…115968` | `ed4b372e…9bc078` |
| status | flashed 13 Nov 2026, boots; never played | flashed 16 Nov 2026, boots; `hardwareTested: true` in the descriptor |
| code layout | in-place, six sites | out-of-line UI, eight sites |
| `imageVersion` | `"1.2"` | `"1.12"` |
| installed FIT sha256 | `af3b3d5c…875d309` | `fac0d102…ebce920` |
| normalized FIT sha256 | `930a672c…f605fafb` | `19c17f1f…62d8be2` |
| built image sha256 | `b374b5e3…a681e222` | `2be1b263…a580059c` |

The two files share the same schema and patch-entry shape
(`offset`/`beforeHex`/`afterHex`), so either project's loader can parse the
other's descriptor. Parsing is not validation: the installed hashes differ.
`fx12/audit_patch_12.py` will parse the re-nanocore file and then reject it on
`imageVersion` (`"1.12"`), before it reaches the hash checks.

`normalized` zeroes the timestamp and every patched/hashed range before
hashing, so it is the descriptor's cross-check on the patch list itself. The
installed FIT hash is the identity of the result.

## Patch map

FIT-relative offsets, both descriptors:

| offset | this repo | re-nanocore | role |
|---:|---:|---:|---|
| `0x044996` | 68 B | 68 B | MOD 10 value formatter |
| `0x08B06A` | 128 B | 120 B | defaults writer + `SPEED`/`MODE` |
| `0x08B0EA` | 1366 B | 1366 B | Velvet Vibrato body → UniVibe |
| `0x0A1A7C` | 15 B | 15 B | effect name |
| `0x0A297C` | 16 B | 16 B | titlecase labels |
| `0x0A2C6C` | 16 B | 16 B | uppercase labels |
| `0x04326E` | — | 4 B | `jal` redirect |
| `0x038D76` | — | 4 B | `jal` redirect |

The two extra entries are call-site redirects into re-nanocore's out-of-line UI
code. They are not missing from this repo's image: this build replaces the
formatter and both label rows in place, so the original call sites still reach
the new code. The difference is a design choice, not a defect — but it means
the installed bytes differ.

The `0x08B06A` entry is the other asymmetry: same start, different length. The
overlapping stock bytes are identical (same input image), but this repo replaces
128 bytes and re-nanocore 120. Do not splice one descriptor's range into the
other.

Because of those two differences, the installed FIT, the normalized FIT, the
final segment CRC (`4de70cba` vs `4b235de2`) and its MD5 all differ.

## The combined v2 image is a different schema

`web/octave-1.2.json` is not a UniVibe patch with more entries. It is a
schema-2 combined image:

| field | value |
|---|---|
| `schemaVersion` | 2 |
| effect | `UNIVIBE_OCTAVE` |
| descriptor sha256 | `a0152234…e10ac3` |
| patches | 81 |
| stock FIT | 759,808 B |
| installed FIT | 797,760 B (+37,952 B) |
| protected offset | 851,968 |
| at `0x08B0EA` | 55 B trampoline, not the 1,366 B body |
| built image sha256 | `7c2656e9…72284511` |

It appends 37,952 bytes, starts the Velvet site with a trampoline, and stops
before the protected final partial sector. It is not a superset of the v1 patch
array; merging the two lists would produce an image neither project built.

`webusb/firmware-lite.js` has a `schemaVersion === 2` validation branch
(`extendedSchema`, `stockFitSize`/`installedFitSize`,
`stockNormalizedSha256`/`installedNormalizedSha256`). `webusb/flash.js` is
pinned to this repo's v1 build and does not use that branch.

## Identify a readback

`fx12/identify_image.py` classifies a complete 868,352-byte partition against any set of
descriptors, offline:

```bash
python fx12/identify_image.py --image <readback> \
    --descriptor fx12/examples/patch-1.2.json \
    --descriptor web/patch-1.2.json \
    --descriptor web/octave-1.2.json
```

It prints `stock` or `installed`, the effect, the firmware version and the image's installed
FIT hash, plus every descriptor that matches. Exit codes: 0 for one identity, 1 for none, 2
for more than one installed identity.

A stock readback matches both v1 descriptors and the v2 descriptor as `stock`; that is
expected, because they share a release, and the two identical v1 matches collapse to one
line. An installed image matches one. If its timestamp differs from the
descriptor's, a v1 image is still classified `installed` and the FIT hash is annotated
`differs from descriptor`; the normalized hash is what decides. The built image hashes in the
tables above therefore only reproduce from the same readback — compare the installed FIT
hash, or let the identifier do it.

## Rules

1. Identify a 1.2 image by `firmwareVersion` plus `stockFitSha256`, not by `imageVersion`,
   filename or size. Both descriptors describe the same stock release: this repo's states
   `"1.2"`, while re-nanocore's leaves it at `"1.12"`, which is what the FIT header still
   contains. `fx12/identify_image.py` does the classification offline.
2. Use the descriptor's own `installedFitSha256` and segment hashes. Never reuse a hash from
   another image, even at the same offsets.
3. Do not merge v1 and v2 patch arrays. v2 changes the code layout and appends data; v1 is a
   fixed-size replacement. A schema-1 image will not classify under a schema-2 descriptor.
4. A loader that accepts a descriptor because the offsets and stock hashes match must still
   verify the installed result. `fx12/audit_patch_12.py`, `fx12/build_from_descriptor.py` and
   `webusb/firmware-lite.js` all do this for v1.
5. Read back before and after. The stock OS hash is `8ed563f1…13a684da`; this repo's 13 Nov
   UniVibe OS hash is `b374b5e3…a681e222`; the 18 Nov combined image is `7c2656e9…72284511`.
6. Keep a saved partition for rollback. `fx12/deploy_12.py restore` writes one back and
   requires `--confirm "RESTORE <first 8 hex>"`; there is no undo without it.

## Provenance

- Stock release: 2026-11-11, package `0bf64603…4d2b103d`.
- This repo's image: built and flashed 13 Nov 2026; boots, but has not been
  played through a guitar and amp; descriptor sha256 `409e85e8…115968`.
- re-nanocore v1: published 16 Nov 2026; descriptor sets `hardwareTested: true`.
- re-nanocore v2: published 16 Nov 2026; descriptor sets `workInProgress: true`
  and `hardwareTested: true`.
- v2 image flashed from this repo: 18 Nov 2026, built from `octave-1.2.json`,
  full-partition readback matched `7c2656e9…72284511`, pedal re-enumerated.
  Not played.
- Neither project's documentation, as of 2026-11-18, reports an audition, a
  recording, or a timing measurement with the effect running. "Flashed and
  boots" is the whole of the public evidence.

Last checked 2026-11-18 against `fx12/examples/patch-1.2.json`,
`web/patch-1.2.json` and `web/octave-1.2.json`.
