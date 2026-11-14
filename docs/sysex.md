# The USB-MIDI SysEx protocol

The pedal exposes a USB-MIDI port called `Nanocore`. Asset slots, names and
storage info are all request/response SysEx over that port.

**The framing and the opcode map are already documented publicly and better than
this file can do it** — this document exists to record the parts that are *not*:

| covering | where |
|---|---|
| full framing, direction bytes, 7-bit packing, request/reply layout | `livtra-nanocore-editor/docs/MIDI_MAPPING_NOTES.md:1021-1052` |
| AMP and IR opcode tables, with verified reply bytes | `nanocore-controller/docs/amp-ir-protocol.md:17-29`, `:182-189` |
| firmware-side handler behaviour | `re-nanocore/firmware/protocol/amp_commands.c` |

Read those first. What follows is the summary needed to follow this repository,
then two corrections.

---

## Framing in brief

```
host → device:   F0  7D  4E 43  70  <7-bit-packed body>  F7
device → host:   F0  7D  4E 43  71  <7-bit-packed body>  F7
```

`7D` is the non-commercial SysEx ID, `4E 43` is ASCII `NC`, `70`/`71` is direction.
The body before packing is:

```
u8   protocol version 02
u16  sequence
u16  command
u16  payload length
     payload
```

The reply body is the same, with a `u8 status` inserted after the command
(`0` = success, non-zero = failure).

Match replies on **sequence and command together**, and discard anything that does
not decode to version 2 — the pedal sends other SysEx traffic.

Data is packed seven bytes to eight: a mask byte whose bit *i* is the MSB of byte
*i*, followed by up to seven bytes masked to `0x7F`. It is an MSB-first bit plane,
not the more common one-byte-per-seven LSB-first scheme. `pack_7bit()` /
`unpack_7bit()` in `cli/nanocore_cli.py` are the reference.

## Opcodes

`0x30` storage info · `0x31` begin · `0x32` chunk · `0x33` commit (with CRC-32) ·
`0x34` abort · `0x35` set name · `0x36` slot info · `0x39` read.
Payload layouts are in the tables linked above; the chunk size the public clients
use is 64 bytes (7,516 bytes = 118 chunks), and reads are windowed at 200 bytes.

---

## Correction 1: AMP slot-info is 31 bytes, IR slot-info is 30

Every public parser assumes a single length, 30. On firmware 1.2 that is right for
one path and wrong for the other — and the two paths share the parser:

| command | record | measured on 1.2 |
|---|---:|---|
| `0x36` AMP slot info | **31 bytes** | all 30 AMP slots |
| `0x56` IR slot info | **30 bytes** | all 30 CAB slots |

So `livtra-nanocore-editor` requiring `payload.length >= 30` and looping the name
to `i < 30` is correct for IR and silently drops the 31st byte for AMP
(`packages/protocol/src/midi/sysex.ts:796-802`). `nanocore-controller` is stricter
and **hard-rejects anything that is not exactly 30 bytes**
(`src/nanocore_controller/nanocore_protocol.py:350-352`): its CAB path works, its
NAM path fails on this firmware. If you have wondered why one of those libraries
reads factory IRs happily but chokes on NAM slots, this is why.

The AMP reply on firmware 1.2 carries a 31st byte, and it is the one that says
what kind of asset the slot holds:

| byte | field |
|---:|---|
| 0 | slot number |
| 1 | present (1 = occupied) |
| 2 | active (1 = selected) |
| 3–6 | asset size, `u32` LE |
| 7–10 | CRC-32 of the asset, `u32` LE |
| 11–12 | format id, `u16` LE |
| 13 | unused in the records we saw |
| 14–29 | name, 16 bytes ASCII, space/NUL padded |
| **30** | **asset type** — `1` = `DDPB`, `3` = `NA2L` |

Offsets 0–29 agree with the public parsers exactly. The disagreement is only the
tail: a 30-byte reader loses `asset_type`, and a strict 30-byte validator refuses
a perfectly valid reply.

Why it matters practically: without byte 30 you cannot tell a `DDPB` slot from a
`NA2L` slot, so you can read a non-NAM asset and try to interpret it as a NAM
model. `3` is the blob described in [`NA2L.md`](NA2L.md).

The IR record stops at byte 29: no asset-type byte, because a CAB slot only ever
holds one kind of thing. A reader that insists on the tail would reject every IR
reply, so treat byte 30 as optional and branch on the command, not on the length.

One trap in that byte 2: it is a **per-slot flag** (`1` = this slot is the selected
one), not the slot index. The selected *index* is the `u16` at offset 16 of `0x50`.
Reading byte 2 as an index gives you `1` for whatever slot is selected, which is
wrong for every slot except slot 1.

## What an IR slot holds

Raw little-endian **float32 mono** samples at 48 kHz. No header, no metadata, no
sample-rate field — the pedal assumes 48 kHz and does not resample or normalise on
write, and it does not require normalised input either. The limit is 4096 bytes =
**1024 samples = 21.33 ms**.

A slot does not have to be full. Read from the factory bank of a 1.2 pedal: 28 of
the 30 slots are exactly 4096 bytes, slot 11 is 2780 bytes (695 samples) and slot 15
is 3764 bytes (941 samples), and all 30 read back with a matching CRC-32. Any
multiple of 4 up to the limit is accepted. Those factory payloads peak between 0.72
and 1.06, which is the evidence that the pedal does not normalise your data.

Turning a WAV into those bytes is a separate problem, and one where the vendor's
algorithm is unknown; [`../ir/`](../ir/) has a converter that states its choices and
an installer that verifies every write byte-for-byte.

**Open question, stated rather than glossed.** `re-nanocore`'s reconstruction of
the firmware 1.10 handler sets the reply length to `0x1e` — 30
(`firmware/protocol/amp_commands.c:503-506`), with an in-RAM stride of `0x20`.
Either 1.2 emits one byte more than 1.10, or the 1.10 reconstruction stops one
short. We only measured 1.2, where 30 would have truncated the record. Do not
assume the tail exists on other firmware versions without checking.

## Correction 2: what the storage-info reply actually contains

Both `0x30` and `0x50` answer exactly 20 bytes. Read straight off a firmware 1.2
pedal:

| bytes | AMP `0x30` | IR `0x50` | reading |
|---|---:|---:|---|
| 0–3 | `466944` | `122880` | slot count × per-slot limit, i.e. total capacity in bytes |
| 4–7 | `4096` | `4096` | **unknown**, and not the per-slot limit |
| 8–11 | `38` | `30` | slot count |
| 12–15 | `12288` | `4096` | per-slot limit in bytes |
| 16–17 | `18` | `9` | active slot, `u16` (`0xFFFF` = none) |

Bytes 0–3 are the part of the first eight that decodes cleanly: `38 × 12,288 =
466,944` and `30 × 4,096 = 122,880`. Bytes 4–7 are `4096` on both paths, so they
are not the AMP limit; on the IR path they coincide with the limit, so IR cannot
tell you which field is which. 12,288 is exactly three 4 KiB sectors and 4,096 is
one, which makes a flash-granularity constant the obvious guess, but that is a
guess and nothing here tests it. `nanocore-controller` lists the whole of
bytes 0–7 as unknown and asks for exactly this measurement
(`docs/amp-ir-protocol.md:89,143,153`); bytes 0–3 are now known, 4–7 are not.

One operational note, measured while installing IRs: `active` was `4` before
writing CAB slots 0–9 and `9` afterwards — it followed the last slot written. Treat
it as mutable by your own writes, not as a stored setting you can rely on across
sessions.

The reply reports `38` and `12288`. Both are already in the public record — but
their relationship is the useful part: `38 × 0x3000 = 0x72000`, i.e. **38 slots
of 12,288 bytes each**, 466,944 bytes total. `map` that onto what you know:

- a `NA2L` NAM blob is 7,516 bytes, comfortably inside a 12,288-byte slot;
- only slots 0–29 are AMP slots — `0x31` refuses beyond 29 — so eight slots of the
  same size belong to a different asset class. Measured here: slots 30–37 hold
  **DDPB** drive-pedal models at 12,242 bytes each, and report `DDPB` in their
  slot-info record while the NAM slots report `NA2L`. That is the distinction
  `asset_type` exists to make.

The AMP slot count and the storage slot count are not the same number. Code that
loops `for slot in range(storage.slot_count)` and writes will walk off the end of
the AMP region.

## What the pedal refuses on its own

Worth knowing before you trust a client-side check: the vendor app is not the only
thing enforcing limits.

- **`0x51` begin validates the length.** Declaring 8,192 bytes (2,048 samples) for a
  slot whose limit is 4,096 is refused with **status `3`** and an empty reply,
  before a single chunk is sent. An oversize IR cannot be written through this
  path even if your client skips the check the vendor app performs.
- **`0x57` is not required for a write to land.** Writing a slot as
  begin → chunks → commit → name and omitting `0x57` stores the bytes, the name and
  the CRC-32 exactly as sent: readback identical, `0x56` reporting the expected
  checksum, all other slots untouched. `0x57`'s purpose is still unknown, but "the
  write does not take without it" is now excluded. Whether it forces the running
  DSP to reload the slot is not something a byte comparison can answer.

## Suggested write sequence

1. `0x30` — confirm slot count and per-slot capacity
2. `0x36` on the target — record what is there now
3. `0x39` — read the existing asset and keep it as a file
4. `0x31` `begin` with the blob length
5. `0x32` × N — 64-byte chunks at increasing offsets
6. `0x33` `commit` with the length and CRC-32
7. `0x35` — set the display name
8. `0x36` — verify size and CRC read back correctly
9. `0x39` — optionally read the asset back and compare byte for byte

Steps 2–3 are the ones people skip. The slot contents are the only copy of what
you are about to overwrite.

## Entering update mode

Firmware flashing is a different mechanism entirely — raw USB DFU, not SysEx
asset transfer. It is entered with:

```
F0 7D 44 46 55 01 F7
```

then wait ~1,200 ms. The pedal detaches and re-enumerates as `33c3:6678`
(`Lupgrader`) instead of `33c3:1301` (`Nanocore`).

There is **no exit command**. Replug the USB cable. See
[`firmware-12.md`](firmware-12.md).
