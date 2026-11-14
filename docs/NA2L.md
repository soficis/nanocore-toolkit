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
  over the whole blob the pedal rejects the write.
- Offsets 24 and 28 were `1` and `0` in every one of the 30 slots we wrote and in
  every slot we read back. Treat them as required constants, not as scratch space.
- Weight count and payload size are carried in the header, but the AMP path we
  exercised is fixed at 1,871 / 7,484. Whether the pedal accepts a different
  architecture through this path is untested — do not assume the header makes it
  general.

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

So the layout above is not an inference from a disassembly — it is what the device
stores and returns.

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
