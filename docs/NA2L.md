# The pedal's NAM asset format: `NA2L`

A NAM capture is normally a `.nam` file — JSON, with an `architecture` string, a
`config` block and arrays of weights. **The pedal never sees any of that.**

When a NAM model arrives at an AMP slot, it arrives as a fixed **7,516-byte binary
blob** whose first four bytes are `NA2L`. There is no JSON, no metadata, no name and
no gain value in it: a 32-byte header followed by 1,871 little-endian `float32`s.

This is the format you have to produce to install a model yourself. Everything else
in this repository depends on getting it right.

---

## Header layout

Little-endian, 32 bytes, `struct` format `<4sHHIIIIII`.

| offset | size | type | value | meaning |
|---:|---:|---|---|---|
| 0 | 4 | `char[4]` | `NA2L` | magic |
| 4 | 2 | `u16` | `1` | format version |
| 6 | 2 | `u16` | `32` | header size in bytes |
| 8 | 4 | `u32` | `48000` | sample rate |
| 12 | 4 | `u32` | `1871` | weight count |
| 16 | 4 | `u32` | `7484` | payload size in bytes (= 1871 × 4) |
| 20 | 4 | `u32` | *varies* | **zlib CRC-32 of the payload** |
| 24 | 4 | `u32` | `1` | constant, meaning unknown |
| 28 | 4 | `u32` | `0` | constant, meaning unknown |

32 + 7,484 = **7,516 bytes**.

Three things worth knowing:

- The checksum at offset 20 is `zlib.crc32(payload) & 0xFFFFFFFF` over the 7,484
  payload bytes only — not over the header, and not a CRC-16. If you compute it
  over the whole blob the pedal rejects the write. The firmware's own helper at
  `0x4007761c` is the standard reflected CRC-32 (polynomial `0xEDB88320`), so the
  algorithm is confirmed, not inferred from the Python side.
- Offsets 24 and 28 were `1` and `0` in every one of the 30 slots we wrote and in
  every slot we read back. Treat them as required constants, not as scratch space.
- Weight count and payload size are carried in the header, but the loader is fixed
  at 1,871 weights and a 23-layer A2 Lite geometry (see below). The header's counts
  are a check the firmware performs, not a way to install a different architecture.

## Payload

1,871 `float32` little-endian values, in order, no padding:

```
config.submodels[0].model.weights
```

That is, the **first submodel** of a NAM A2 `SlimmableContainer`.

## Which `.nam` files map onto this exactly

The shape that converts 1:1 is the canonical two-submodel A2 container:

```json
{
  "version": "0.7.0",
  "architecture": "SlimmableContainer",
  "config": { "in_channels": 1, "out_channels": 1, "submodels": [ ... ] },
  "weights": [],
  "sample_rate": 48000,
  "metadata": { ... }
}
```

with:

- `config.submodels[0]` — the **Lite** model, `"max_value": 0.5`, 1,871 weights
- `config.submodels[1]` — the **Full** model, `"max_value": 1.0`
- top-level `"weights": []`

The pedal's header carries one weight vector, so it gets submodel 0. That is the
whole point of the "A2 Lite" naming: the device runs the 0.5 submodel.

If your file is a single-submodel model, or the Lite half is not at index 0, or
`max_value` is something else, the conversion is not meaningful even though the
JSON looks similar. The reference converter **refuses** rather than guessing:

```
ValueError: not a two-submodel SlimmableContainer
ValueError: first submodel is not max_value 0.5
ValueError: first submodel has 4109 weights, expected 1871
```

When you rebuild the container yourself, take the outer `metadata` from the Lite
file, not the Full one — that is the submodel the pedal actually runs, so that is
the capture the display and provenance should describe.

## What the pedal does *not* store

Worth stating explicitly, because it is the usual source of confusion:

- **no name.** The slot's 16-character display name is a separate SysEx message
  (opcode `0x35`). Two slots can hold identical blobs and be labelled differently.
- **no `.nam` metadata.** No architecture, no date, no license, no model name.
- **no input/output gain.** Level matching is yours to do.
- **no impulse response.** Cabs are a separate asset type in a separate slot class.

## Round-trip evidence

30 models were converted, written to AMP slots 0–29, then read back with the read
opcode (`0x39`) and compared:

- 30/30 returned 7,516 bytes
- 30/30 CRC-32 in the returned header matched the CRC-32 of the returned payload
- 30/30 matched the blob we sent, byte for byte
- 30/30 carried the 16-character name we wrote

So the layout above is not inferred from a disassembly - it is what the device
stores and returns, and the firmware's own parser agrees.

## What the firmware does with it

The blob is not just stored. The 1.2 image parses it, allocates run-time state for it, and
runs it on the AMP path. From the decoded image:

- **Validator at `0x40077658`.** It checks the header field for field: magic, version 1,
  header size 32, sample rate 48000, weight count 1,871, payload size 7,484, then the
  CRC-32. `0x4007e7fa` recognises `NA2L` as its own asset kind and dispatches it separately
  from the `DDPB` drive models; the runtime kind it installs is 4, a different number from
  the kind in the slot record.
- **Fixed geometry.** The network is 3 input channels, 64-sample blocks, 23 layers. The
  dilations at `0x400917d8` are `1,3,7,17,41,101,239` twice, then `1,13`, then the same
  seven again; the kernel sizes at `0x40091808` are fourteen 6s, two 15s and seven 6s. The
  activation is LeakyReLU with `0.01`, and the output head is a 16-tap, 3-input convolution
  with a scalar scale. This is the A2 Lite shape, not a general NAM runtime.
- **15× more state than weights.** The weights are 7,484 bytes. The run-time state is
  **115,916 bytes**, allocated as 115,947 bytes and 32-byte aligned (`0x4007779e`,
  `0x400777a6`). It comes from heap 2 (`heap_model`), a 124,832-byte DTCM region at
  `0x30061860..0x30080000` described at `0x400a1e08`.
- **It pre-warms on load.** `0x40077880` runs 100 zero blocks of 64 samples - 6,400 samples,
  or 133 ms at 48 kHz - before the model is marked ready.
- **The hot loop is not in ITCM.** The DSP body sits at `0x30080000`, in the code/data
  segment, while the state sits in DTCM. Cycle cost was not measured, so there is no claim
  here about real-time headroom.

What this does **not** establish: that a particular `.nam` file converts to these numbers,
that the pedal sounds like the reference implementation, or that a host converter produces
the same weights as the vendor's tool. The `.nam` → `NA2L` step happens on the host; the
firmware only shows what it will accept once you get there.

## Reference code

About 40 lines, in `cli/nanocore_cli.py`:

- `build_na2l()` — JSON → 7,516-byte blob, with the three refusal cases above
- `validate_na2l()` — length, magic, version, header size, sample rate, weight
  count, payload size, CRC-32 and the two trailing constants
- `pack_7bit()` / `unpack_7bit()` — the transport encoding, see
  [`sysex.md`](sysex.md)

Use `validate_na2l()` on anything before you send it. It catches the one mistake
that is otherwise silent: a correct-looking blob whose CRC was computed over the
wrong range.
