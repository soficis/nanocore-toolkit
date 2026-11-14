# IR (CAB) slots

Tools to back up, build for, install to and verify the pedal's 30 CAB slots, over the
same USB-MIDI SysEx transport as `../cli/`.

The protocol side of this is documented in [`../docs/sysex.md`](../docs/sysex.md) —
opcodes `0x50`–`0x57` and `0x59`, the 30-byte slot record, and the two limits the
pedal enforces itself. This folder is the part those docs do not cover: what the
payload actually is, and how to get a WAV into it.

## What a CAB slot holds

Measured on firmware 1.2:

| property | value |
|---|---|
| slots | 30, indices 0–29 |
| bytes per slot | 4096 (enforced by the pedal: `0x51` declaring more is refused with status 3) |
| format | raw little-endian **float32 mono**, no header |
| samples | up to 1024, i.e. 21.33 ms at 48 kHz |
| sample rate | 48 kHz assumed; not stored, not resampled |
| normalisation | none applied by the pedal, and none required |
| checksum | zlib CRC-32 over the whole slot, sent at `0x53`, reported by `0x56` |
| name | 15 printable ASCII characters via `0x55` |

A slot does not have to be full — any multiple of 4 bytes up to 4096 is accepted. The
factory bank of a 1.2 pedal has 28 slots at exactly 4096 bytes, one at 2780 (695
samples) and one at 3764 (941 samples), and its payloads peak anywhere from 0.72 to
1.06, which is the practical proof that the pedal neither normalises nor expects you
to.

## Quick start

```sh
# 1. Save every slot first. The upload refuses to run without this manifest.
python install_ir.py backup --output ir_backup

# 2. Convert 48 kHz WAVs into slot data. Writes *.ir.bin plus ir_build.json.
python wav_to_ir.py path/to/wavs -o ir_build

# 3. Read the plan. Nothing is written without --yes.
python install_ir.py upload ir_build --require-backup ir_backup

# 4. Write, verify each slot against what was sent, then confirm in a fresh process.
python install_ir.py upload ir_build --require-backup ir_backup --yes
python install_ir.py verify ir_build
```

`wav_to_ir.py` assigns slots sequentially from `--start` (default 0) in filename order,
so ten files starting at 0 land in slots 0–9.

## The converter is a choice, not the vendor algorithm

Nothing here claims to reproduce what the vendor app does to a WAV before it uploads
it. As far as could be established, that step is not documented anywhere, and it is
not in the pedal's native library either. What `wav_to_ir.py` does, and why:

1. **Decode** PCM 8/16/24/32-bit or 32-bit float, then average channels to mono.
   The pedal is mono, so a stereo cab IR has to be summed somewhere.
2. **Require 48 kHz** rather than resample. Silently resampling an IR changes it; a
   clear error tells you to convert it yourself, with a tool you trust.
3. **Drop the lead-in**: skip samples before the first one reaching 1 % of the file's
   peak, so a silent preamble does not consume the window.
4. **Keep the first 1024 samples.**
5. **Fade the last 64 samples** with a raised cosine when the file was longer, so the
   cut does not click.
6. **Normalise to a peak of 1.0** and pack as float32.

Step 6 is the one to think about. Normalising makes levels predictable but erases the
relative loudness between captures; pass `--no-normalise` to keep the source's own
peak, which is what the factory IRs look like.

The tool prints the percentage of each file's energy that survived step 4. On the ten
IRs this was written for, that was 98–99.9 %, because those files had decayed within
21 ms. A file that reports 60 % is telling you the pedal will hear only the first
fifth of it — in that case a shorter, well-chosen window of the same IR usually sounds
better than a naive truncation, and that judgement is yours, not the tool's.

## Verification, and what it does not prove

Every write is compared byte-for-byte against the payload that was sent, in the same
session (`--yes` path), and `verify` re-reads all listed slots in a fresh process and
compares again. The ten IRs currently in the author's pedal were built with this
converter and re-checked this way: all ten payloads reproduced byte-identically from
the source WAVs, and `verify` returned `10/10 slots verified`.

That proves the bytes landed. It does not prove the IR *sounds* right, and it cannot:
between the WAV and the pedal there are still your choice of window, your choice about
normalising, and the fact that 21.33 ms is short for a cabinet IR. Use your ears on
one slot before writing ten.

## Safety notes

- **The backup gate is not decorative.** `upload` refuses without
  `IR_FACTORY_MANIFEST.json`, because the pedal has no undo and a CAB slot cannot be
  reconstructed from anything else.
- **Writing a slot changes every preset that uses it.** The pedal's `0x50` reply
  reports which slot is currently selected; that selection will follow your writes
  (`active` moved from 4 to 9 while installing ten slots), so do not read it as a
  stored setting.
- **Quit the vendor app first.** ToneCommand holds the MIDI port, and only one process
  can talk to the pedal at a time.
- **Normal mode only.** These commands need the pedal enumerated as `33c3:1301`. In
  update mode (`33c3:6678`) there is no SysEx to talk to.
- **The pedal rejects oversize data itself**, so a buggy client cannot corrupt the
  next slot by writing long: `0x51` refuses with status 3 before any chunk is sent.

## Files

| file | what it is |
|---|---|
| `wav_to_ir.py` | WAV → slot payloads, plus `ir_build.json` for the installer |
| `install_ir.py` | `backup`, `upload` (dry by default) and `verify` subcommands |
| `../cli/nanocore_cli.py` | the SysEx transport both scripts import (`--port` is honoured) |

Requires Python 3.9+ with `mido` and `python-rtmidi`, the same as `../cli/`.
