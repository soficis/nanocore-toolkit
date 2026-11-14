# NOTICE

`nanocore-toolkit` is licensed under the GNU General Public License v3.0 — see `LICENSE`.
This file records what is *not* ours, and what is not distributed here at all.

Copyright (C) 2026 soficis.

---

## 1. Third-party MIT components

Three public projects informed this work. All are MIT-licensed. GPLv3 is one-way compatible
with MIT: MIT code may be incorporated into a GPLv3 work, provided the MIT notice is retained.
That is what this section does.

### 1.1 re-nanocore — https://github.com/jcuhnoio/re-nanocore

Vendored and derived files:

| file here | provenance |
|---|---|
| `webusb/dfu.js` | Copied **unmodified** from `re-nanocore/web/dfu.js`. sha256 `281f2858a6be1e38de1c2843957a8c2aac881639b52b9f4f12613733cc03e2a5`, 10,216 bytes. |
| `webusb/firmware-lite.js` | Copy of `re-nanocore/web/firmware.js` with **one line changed**: `'1.2'` added to the firmware-version whitelist in `schema()`. Original sha256 `0cd263b0ae62191f629401ba707ccf4f09dc85f089d4131d71a9da817aaedc7e` (10,354 bytes); modified sha256 `76f2e288b8c08145a33a18998c8518801ffee17915d2b55a929a263ef5c100c3` (10,360 bytes). The change is a policy string, not an integrity check — see `webusb/README.md`. |
| `fx12/vibe_format_12.S`, `fx12/vibe_defaults_12.S` | Derived from the corresponding 1.10/1.12 blobs in `re-nanocore/tools/custom_fx/`, relinked for the firmware 1.2 addresses. |
| `fx12/build_patch_12.py`, `build_plan_12.py`, `audit_patch_12.py`, `audit_refs.py`, `diff_report_12.py` | Written here, but built against re-nanocore's FIT container, descriptor schema and partition map. |

```
MIT License

Copyright (c) 2026 jcuhnoio and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 1.2 nanocore-controller — https://github.com/dvilelaf/nanocore-controller

No code from this project is included. It is credited because its
`docs/amp-ir-protocol.md` is the most complete public AMP/IR opcode table, and its verified
`0x30` reply is the reference for the storage report. The web editor under that project's
`web/` directory carries its own separate MIT license (Copyright (c) 2026 Luca Nenni).

```
MIT License

Copyright (c) 2026 dvilelaf

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 1.3 livtra-nanocore-editor — https://github.com/lucanenni/livtra-nanocore-editor

No code from this project is included. Credited for `docs/MIDI_MAPPING_NOTES.md`, whose
record of the bit-plane packing scheme and the observed 64-byte chunk size is the best public
description of the host protocol.

```
MIT License

Copyright (c) 2026 Luca Nenni

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 1.4 Neural Amp Modeler

`.nam` container files are produced by [Neural Amp Modeler](https://github.com/sdatkinson/NeuralAmpModelerCore)
(MIT). This project reads that format; it does not redistribute the application.

---

## 2. re-nanocore's license scope — important

re-nanocore's MIT grant is **narrower than its LICENSE file suggests**. Its
`LICENSE_SCOPE.md` states that MIT covers only that project's original work — its tools,
decoders, patch builders, uploaders, browser installer, documentation, and custom effects
including UniVibe — and explicitly **excludes**:

- vendor firmware images, bootloaders, application binaries, model weights, impulse responses
  and UI assets;
- decompiled vendor code and vendor-derived reconstructions (`firmware/boot/`, `runtime/`,
  `storage/`, `formats/`, `update/`, `protocol/`, `unresolved/`, `versions/`, and
  `firmware/custom-fx/src/chorus.c`);
- and, in its own words, vendor-derived bytes, instructions and excerpts embedded in patch
  manifests and disassembly. Generated patch artifacts "can mix original custom payloads with
  vendor-derived material; the MIT grant covers only the original contributions within them."

The scope document asks that it "be kept with mixed research or patch distributions so the MIT
grant is not mistaken for a vendor firmware license." This file is that acknowledgement.

**Consequence for this repository:** the GPL-3.0 grant in `LICENSE` covers original work only.
It does not, and cannot, extend to vendor firmware or to vendor-derived material. Where files
here contain a mix — most notably `fx12/examples/patch-1.2.json`, whose `beforeHex` fields
necessarily quote bytes from a vendor image — the license covers the descriptor structure, the
patch logic and the documentation, not the quoted vendor bytes.

---

## 3. What is not distributed here

For both licensing and safety reasons, this repository ships **tooling only**:

- no vendor firmware images (`.fmu` or otherwise), no bootloader, no OS readback;
- no patched OS partition — `fx12/examples/patch-1.2.json` is a descriptor, and the images it
  refers to must be built from your own readback;
- no `.nam` model files and no impulse responses. Captures from commercial sources are
  typically licensed for personal use only and are not redistributable;
- no decompiled vendor source.

If you build an image with these tools, the resulting binary is your responsibility, and its
provenance is your own device's firmware.

---

## 4. Trademarks

"Livtra" and "Nanocore" are trademarks of their respective owner. "Neural Amp Modeler" and
"NAM" belong to their respective authors. This project is not affiliated with, endorsed by, or
supported by any of them. Custom effects and replacement amp models are unofficial; a failed
write may require vendor recovery tools or service.

---

## 5. Reporting a licensing problem

If you believe something here is misattributed or should not be present, open an issue. Files
will be removed rather than disputed.
