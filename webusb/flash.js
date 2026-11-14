// Nanocore OS 1.2 UniVibe -- WebUSB FLASH page logic (WRITE PATH).
//
// Three stages, and nothing happens by accident:
//   Stage 0  plan only. Reads three local files, hashes them, and computes the
//            exact 64 KiB erase set. No USB object is constructed, no device is
//            requested, no transfer is issued.
//   Stage 1  enter update mode (MIDI SysEx), same handshake as read-backup.js.
//   Stage 2  flash. Gated on Stage 0 passing AND the exact confirmation phrase.
//
// Every assertion below is repeated immediately before the first USB call, so a
// stale DOM, a swapped file or a mutated buffer cannot slip through.
//
// ./dfu.js is an unmodified copy and does all the USB work. ./firmware-lite.js is
// a local copy of re-nanocore/web/firmware.js with '1.2' added to its firmware
// version whitelist (one line); see README.md. The repo copy is untouched.
//
// program() itself performs no descriptor validation, so validateImage() is the
// ONLY authentication that `target` is the exact image we built.

const BOOTLOADER_SYSEX = [0xf0, 0x7d, 0x44, 0x46, 0x55, 0x01, 0xf7];
const BOOTLOADER_WAIT_MS = 1200;

/** Build identity. These constants, not the files on disk, are the anchor. */
export const BUILD = Object.freeze({
  firmwareVersion: '1.2',
  effect: 'UNIVIBE',
  osSize: 868352,
  fitSize: 759808,        // FIT payload; sha256 over [0, fitSize) is the installed FIT hash
  sector: 65536,
  finalSectorStart: 851968, // 0xD0000: final PARTIAL sector, must never be erased
  targetSha256: 'b374b5e364c73100f8678b61edda7916a1c62c7e2320d8083d098c72a681e222',
  targetFitSha256: 'af3b3d5c83a0213138aed66bdb1f1685afa2ed4bd94a250ce852e5985875d309',
  stockSha256: '8ed563f16242610d634c6659cb0002b913a6a8ff89a1ffc9b0a2d4e913a684da',
  descriptorSha256: '409e85e8a47e68d274db21b3f4dc233483a39f03c5a911a1894cf82b8e115968',
  expectedSectors: [0x000000, 0x040000, 0x080000, 0x0a0000],
  confirmation: 'FLASH UNIVIBE 1.2',
});

/* ------------------------------------------------------------------ pure core */

const bytesEqual = (a, b) => {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
};

export async function sha256Hex(value) {
  if (!(value instanceof Uint8Array)) throw new Error('sha256Hex expects a Uint8Array');
  const digest = await globalThis.crypto.subtle.digest('SHA-256', value);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Same sector walk as RawOS.program(): a sector is planned iff its 64 KiB slices differ. */
export function planSectors(expected, target, sector = BUILD.sector) {
  if (!(expected instanceof Uint8Array) || !(target instanceof Uint8Array)) throw new Error('planSectors expects Uint8Arrays');
  if (expected.length !== target.length) throw new Error(`planSectors length mismatch: ${expected.length} vs ${target.length}`);
  if (!Number.isSafeInteger(sector) || sector <= 0) throw new Error('planSectors expects a positive sector size');
  const sectors = [];
  for (let offset = 0; offset < expected.length; offset += sector) {
    if (!bytesEqual(expected.subarray(offset, offset + sector), target.subarray(offset, offset + sector))) sectors.push(offset);
  }
  return sectors;
}

async function validateImageLite(value, patch) {
  const { validateImage } = await import('./firmware-lite.js');
  return validateImage(value, patch);
}

/**
 * Stage 0. Pure analysis of three in-memory files. Never touches USB.
 * Returns a report; `ok` is true only if every check passed.
 */
export async function inspect({ stock, target, patch }) {
  const rows = [];
  const add = (label, ok, detail) => rows.push({ label, ok: !!ok, detail: detail === undefined ? '' : String(detail) });

  add('stock readback is a Uint8Array', stock instanceof Uint8Array, stock instanceof Uint8Array ? `${stock.constructor.name} ${stock.length} B` : 'missing');
  add('patched image is a Uint8Array', target instanceof Uint8Array, target instanceof Uint8Array ? `${target.constructor.name} ${target.length} B` : 'missing');
  add('descriptor is a JSON object', patch && typeof patch === 'object', patch && typeof patch === 'object' ? `schemaVersion ${patch.schemaVersion}` : 'missing');

  const sizesOk = stock instanceof Uint8Array && target instanceof Uint8Array &&
    stock.length === BUILD.osSize && target.length === BUILD.osSize;
  add(`both images are exactly ${BUILD.osSize.toLocaleString('en-US')} bytes`, sizesOk,
    `${stock?.length ?? '?'} / ${target?.length ?? '?'}`);

  const stockSha = stock instanceof Uint8Array ? await sha256Hex(stock) : '';
  const targetSha = target instanceof Uint8Array ? await sha256Hex(target) : '';
  const targetFitSha = target instanceof Uint8Array && target.length >= BUILD.fitSize
    ? await sha256Hex(target.subarray(0, BUILD.fitSize)) : '';
  const descriptorSha = patch ? await sha256Hex(new TextEncoder().encode(JSON.stringify(patch, null, 2) + '\n')) : '';

  add('patched image sha256 matches the build', targetSha === BUILD.targetSha256, targetSha);
  add('patched image FIT hash [0, 759808) matches the build', targetFitSha === BUILD.targetFitSha256, targetFitSha);
  add('stock readback sha256 matches the recorded rollback source', stockSha === BUILD.stockSha256, stockSha);
  add('descriptor is the expected firmware-1.2 manifest', patch?.firmwareVersion === BUILD.firmwareVersion, patch?.firmwareVersion ?? '');
  add('descriptor osSize is 868352', patch?.osSize === BUILD.osSize, patch?.osSize ?? '');

  // Content authentication: this is the only proof that `target` is our image.
  let targetState = null;
  let stockState = null;
  try {
    targetState = await validateImageLite(target, patch);
    add('validateImage(target) identifies an installed image', targetState.kind === 'installed', JSON.stringify(targetState));
  } catch (error) {
    add('validateImage(target) identifies an installed image', false, error.message);
  }
  try {
    stockState = await validateImageLite(stock, patch);
    add('validateImage(stock) identifies a stock image', stockState.kind === 'stock', JSON.stringify(stockState));
  } catch (error) {
    add('validateImage(stock) identifies a stock image', false, error.message);
  }

  let sectors = [];
  let sectorsOk = false;
  try {
    sectors = planSectors(stock, target);
    sectorsOk = true;
  } catch (error) {
    add('sector plan could be computed', false, error.message);
  }
  if (sectorsOk) {
    add('sector plan is exactly the four predicted sectors', JSON.stringify(sectors) === JSON.stringify(BUILD.expectedSectors),
      sectors.map((o) => '0x' + o.toString(16).padStart(6, '0')).join(', '));
    const partial = sectors.filter((offset) => offset + BUILD.sector > BUILD.osSize);
    add(`no planned sector touches the final partial sector ${BUILD.finalSectorStart.toLocaleString('en-US')}..${BUILD.osSize.toLocaleString('en-US')}`,
      partial.length === 0, partial.length ? 'WOULD ERASE ' + partial.map((o) => '0x' + o.toString(16)).join(', ') : 'untouched');
    add('every planned sector lies inside the OS partition', sectors.every((offset) => offset >= 0 && offset < BUILD.osSize),
      sectors.length + ' sector(s)');
  }

  const ok = rows.every((r) => r.ok);
  return {
    ok,
    rows,
    sectors,
    stockSha,
    targetSha,
    targetFitSha,
    descriptorSha,
    targetState,
    stockState,
    totalErased: sectors.length * BUILD.sector,
    finalSectorUntouched: sectors.every((offset) => offset + BUILD.sector <= BUILD.osSize),
  };
}

/**
 * Last gate before the first USB transfer. Re-derives everything from the
 * buffers that will actually be handed to program(). Throws on any mismatch.
 */
export async function assertFlashPreconditions({ expectedCurrent, target, patch }) {
  const fail = (message) => { throw new Error(message); };

  if (!(target instanceof Uint8Array)) fail('Target is not a Uint8Array.');
  if (target.length !== BUILD.osSize) fail(`Target is ${target.length} bytes, expected ${BUILD.osSize}.`);
  const targetSha = await sha256Hex(target);
  if (targetSha !== BUILD.targetSha256) fail(`Target sha256 ${targetSha} is not the recorded build.`);
  const targetFitSha = await sha256Hex(target.subarray(0, BUILD.fitSize));
  if (targetFitSha !== BUILD.targetFitSha256) fail(`Target FIT sha256 ${targetFitSha} is not the recorded build.`);

  if (!(expectedCurrent instanceof Uint8Array)) fail('Expected-current image is not a Uint8Array.');
  if (expectedCurrent.length !== BUILD.osSize) fail(`Expected-current image is ${expectedCurrent.length} bytes, expected ${BUILD.osSize}.`);
  const stockSha = await sha256Hex(expectedCurrent);
  if (stockSha !== BUILD.stockSha256) fail(`Expected-current sha256 ${stockSha} is not the stock readback.`);

  const targetState = await validateImageLite(target, patch);
  if (targetState.kind !== 'installed') fail(`Target validates as "${targetState.kind}", not "installed".`);
  const stockState = await validateImageLite(expectedCurrent, patch);
  if (stockState.kind !== 'stock') fail(`Expected-current validates as "${stockState.kind}", not "stock".`);

  return { targetSha, targetFitSha, stockSha, targetState, stockState };
}

/* ------------------------------------------------------------------ ui helpers */

const $ = (id) => document.getElementById(id);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function log(line) {
  const box = $('log');
  const stamp = new Date().toISOString().slice(11, 19);
  box.textContent += `${stamp}  ${line}\n`;
  box.scrollTop = box.scrollHeight;
}

function status(id, text, kind) {
  const node = $(id);
  node.textContent = text;
  node.className = 'status' + (kind ? ' ' + kind : '');
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

const hex = (offset) => '0x' + offset.toString(16).padStart(6, '0');

/* ------------------------------------------------------------- page state */

const state = { stock: null, target: null, patch: null, report: null, busy: false };

function refreshGate() {
  const armed = state.report?.ok === true;
  const phrase = $('confirm').value.trim() === BUILD.confirmation;
  $('flash').disabled = !armed || !phrase || state.busy;
  $('armed').textContent = armed ? 'Stage 0 passed.' : 'Stage 0 has not passed.';
  $('armed').className = 'status ' + (armed ? 'ok' : '');
}

/* --------------------------------------------------- stage 0: plan only */

async function readPickedFile(id) {
  const input = $(id);
  const file = input.files && input.files[0];
  if (!file) throw new Error(`Choose a file for "${id}".`);
  const buffer = new Uint8Array(await file.arrayBuffer());
  log(`Read ${id}: ${file.name}, ${buffer.length} bytes.`);
  return buffer;
}

async function analyse() {
  const button = $('analyse');
  button.disabled = true;
  $('plan').hidden = true;
  state.report = null;
  refreshGate();
  status('plan-status', 'Hashing both images. No USB access in this stage.');
  try {
    state.stock = await readPickedFile('stock-file');
    state.target = await readPickedFile('target-file');
    const raw = await readPickedFile('patch-file');
    try {
      state.patch = JSON.parse(new TextDecoder().decode(raw));
    } catch (error) {
      throw new Error(`Descriptor is not valid JSON: ${error.message}`);
    }

    // The descriptor hash is checked against the file the user actually picked.
    const descriptorSha = await sha256Hex(raw);
    const report = await inspect({ stock: state.stock, target: state.target, patch: state.patch });
    report.descriptorSha = descriptorSha;
    const descriptorOk = descriptorSha === BUILD.descriptorSha256;
    report.rows.push({
      label: 'descriptor sha256 matches the recorded manifest',
      ok: descriptorOk,
      detail: descriptorSha,
    });
    report.ok = report.rows.every((r) => r.ok);
    state.report = report;

    renderReport(report);
    status('plan-status', report.ok
      ? 'Stage 0 passed. Nothing has touched the pedal.'
      : 'Stage 0 FAILED. Do not continue; fix the files above.', report.ok ? 'ok' : 'bad');
    log(report.ok ? 'Stage 0 passed; no USB call was made.' : 'Stage 0 failed; no USB call was made.');
  } catch (error) {
    status('plan-status', `Stage 0 failed: ${error.message}`, 'bad');
    log(`Stage 0 failed: ${error.message}`);
  } finally {
    button.disabled = false;
    refreshGate();
  }
}

function renderReport(report) {
  const rows = $('checks');
  rows.textContent = '';
  for (const row of report.rows) {
    const dt = document.createElement('dt');
    dt.textContent = (row.ok ? 'PASS  ' : 'FAIL  ') + row.label;
    dt.className = row.ok ? 'pass' : 'fail';
    const dd = document.createElement('dd');
    dd.textContent = row.detail;
    rows.append(dt, dd);
  }

  $('v-stock-len').textContent = `${state.stock.length.toLocaleString('en-US')} bytes`;
  $('v-target-len').textContent = `${state.target.length.toLocaleString('en-US')} bytes`;
  $('v-stock-sha').textContent = report.stockSha;
  $('v-target-sha').textContent = report.targetSha;
  $('v-target-fit').textContent = report.targetFitSha;
  $('v-descriptor-sha').textContent = report.descriptorSha;

  const list = $('sector-list');
  list.textContent = '';
  for (const offset of report.sectors) {
    const li = document.createElement('li');
    li.textContent = `${hex(offset)} .. ${hex(offset + BUILD.sector - 1)}  (${BUILD.sector.toLocaleString('en-US')} bytes)`;
    list.append(li);
  }
  $('sector-count').textContent = `${report.sectors.length} sector(s), ${report.totalErased.toLocaleString('en-US')} bytes erased`;
  $('final-sector').textContent = report.finalSectorUntouched
    ? `The final partial sector ${BUILD.finalSectorStart.toLocaleString('en-US')}..${BUILD.osSize.toLocaleString('en-US')} is NOT in the erase set: untouched.`
    : 'WARNING: the plan would erase the final partial sector. Do not proceed.';
  $('final-sector').className = 'status ' + (report.finalSectorUntouched ? 'ok' : 'bad');
  $('plan').hidden = false;
}

/* ------------------------------------------------ stage 1: enter update mode */

async function requestMidiAccess() {
  try {
    return await navigator.requestMIDIAccess({ sysex: true });
  } catch (error) {
    log(`MIDI access with SysEx was refused (${error.name}); retrying without SysEx.`);
    return navigator.requestMIDIAccess();
  }
}

async function enterUpdateMode() {
  const button = $('enter');
  button.disabled = true;
  status('enter-status', 'Requesting MIDI access...');
  try {
    if (!navigator.requestMIDIAccess) throw new Error('This browser has no Web MIDI. Use desktop Chrome or Edge.');
    const access = await requestMidiAccess();
    const ports = Array.from(access.outputs.values());
    const named = ports.map((port) => port.name || '(unnamed output)');
    log(`MIDI outputs visible: ${named.length ? named.join(', ') : 'none'}`);
    const matches = ports.filter((port) => /nanocore/i.test(port.name || ''));
    if (matches.length === 0) {
      throw new Error(`No MIDI output port name matches /nanocore/i. Seen: ${named.join(', ') || 'none'}. ` +
        'Check the USB cable, and that ToneCommand is closed.');
    }
    if (matches.length > 1) {
      throw new Error(`${matches.length} MIDI output ports match /nanocore/i. Close other apps or unplug the extra unit.`);
    }
    const port = matches[0];
    log(`Using MIDI output "${port.name}".`);
    await port.open();
    port.send(BOOTLOADER_SYSEX);
    log('Bootloader request sent: F0 7D 44 46 55 01 F7.');
    await delay(BOOTLOADER_WAIT_MS);
    port.close();
    log('MIDI port closed.');
    status('enter-status',
      'Sent. Wait 5-10 s for the pedal to come back as an update-mode device (33c3:6678), then go to Stage 2.',
      'ok');
  } catch (error) {
    log(`Stage 1 failed: ${error.message}`);
    status('enter-status', `Stage 1 failed: ${error.message}`, 'bad');
  } finally {
    button.disabled = false;
  }
}

/* ------------------------------------------------------------ stage 2: flash */

function renderProgress(entry) {
  const pct = entry.total > 0 ? Math.min(100, (entry.completed / entry.total) * 100) : 0;
  $('fill').style.width = pct.toFixed(1) + '%';
  $('percent').textContent = pct.toFixed(1) + '%';
  const names = { preflight: 'Preflight read', write: 'Erasing / writing', verify: 'Verify read', complete: 'Complete' };
  $('phase').textContent = names[entry.phase] || entry.phase;
}

async function flash() {
  if (state.busy) return;
  const button = $('flash');
  $('flash-result').hidden = true;

  if (state.report?.ok !== true) {
    status('flash-status', 'Stage 0 has not passed. Nothing was sent.', 'bad');
    return;
  }
  if ($('confirm').value.trim() !== BUILD.confirmation) {
    status('flash-status', `Type "${BUILD.confirmation}" exactly. Nothing was sent.`, 'bad');
    return;
  }

  state.busy = true;
  button.disabled = true;
  let transport = null;
  try {
    if (!navigator.usb) throw new Error('This browser has no WebUSB. Use desktop Chrome or Edge on the localhost URL.');

    // ---- last gate, on the exact buffers that will be used ------------------
    status('flash-status', 'Re-checking the images before any USB call...');
    const { targetSha, stockSha } = await assertFlashPreconditions({
      expectedCurrent: state.stock,
      target: state.target,
      patch: state.patch,
    });
    log(`Pre-flight assertions passed: target ${targetSha.slice(0, 16)}..., expected-current ${stockSha.slice(0, 16)}...`);

    const { WebUSBTransport, RawOS, USB_FILTER } = await import('./dfu.js');
    status('flash-status', 'Waiting for you to pick the 33c3:6678 device...');
    const device = await navigator.usb.requestDevice({ filters: [USB_FILTER] });
    log(`Selected USB device ${device.vendorId.toString(16)}:${device.productId.toString(16)} "${device.productName || ''}".`);

    transport = await WebUSBTransport.open(device);
    log('DFU interface claimed.');
    const os = new RawOS(transport, { onProgress: renderProgress });

    status('flash-status', 'Reading the live partition for preflight. Do not unplug the pedal.');
    const receipt = await os.program({
      expectedCurrent: state.stock,
      target: state.target,
      // Runs AFTER program() has byte-compared the live partition to
      // expectedCurrent and BEFORE anything is erased. This is the only copy of
      // the pre-write device state, so save it here, not later.
      backupSaved: async (verifiedLive) => {
        if (!(verifiedLive instanceof Uint8Array) || verifiedLive.length !== BUILD.osSize) {
          throw new Error('Verified live readback has the wrong shape; refusing to write.');
        }
        if (!bytesEqual(verifiedLive, state.stock)) {
          throw new Error('Verified live readback is not byte-identical to the stock image on disk; refusing to write.');
        }
        const sha = await sha256Hex(verifiedLive);
        if (sha !== BUILD.stockSha256) throw new Error(`Verified live readback sha256 ${sha} is unexpected; refusing to write.`);
        log(`Live partition verified against the stock readback (${sha.slice(0, 16)}...). Saving pre-flash backup.`);
        saveBlob(new Blob([verifiedLive], { type: 'application/octet-stream' }),
          'nanocore-preflash-verified-backup.bin');
        return true;
      },
      onVerified: async (actual, deviceReceipt) => {
        log('Post-write readback matches the target exactly. Saving files.');
        saveBlob(new Blob([actual], { type: 'application/octet-stream' }), 'nanocore-after-univibe.bin');
        const full = {
          ...deviceReceipt,
          builtFrom: BUILD.targetSha256,
          stockSha256: BUILD.stockSha256,
          installedFitSha256: BUILD.targetFitSha256,
          descriptorSha256: BUILD.descriptorSha256,
          confirmationPhrase: BUILD.confirmation,
          verifiedAtUtc: new Date().toISOString(),
          userAgent: navigator.userAgent,
          note: 'Offline build. Bootloader acceptance of the recomputed seg3 crc32/md5 and post-flash audio behaviour are not proven by this receipt.',
        };
        saveBlob(new Blob([JSON.stringify(full, null, 2) + '\n'], { type: 'application/json' }),
          'nanocore-after-univibe.receipt.json');
      },
    });

    $('v-receipt').textContent = JSON.stringify(receipt, null, 2);
    $('flash-result').hidden = false;
    status('flash-status', 'Done and verified. The pedal will reboot on its own. Unplug only after it is back.', 'ok');
    log('Flash complete and verified against the target image.');
  } catch (error) {
    // No retry, no resume: one attempt only.
    log(`Stage 2 failed: ${error.message}`);
    status('flash-status',
      `${error.message}\n\nDo not unplug and do not retry blindly. The pedal stays in its update-mode bootloader; ` +
      'recover by reading it again (read-backup.html) or by running the official updater.', 'bad');
  } finally {
    if (transport) {
      try { await transport.close(); } catch (error) { log(`Close reported: ${error.message}`); }
      log('USB interface released.');
    }
    state.busy = false;
    refreshGate();
  }
}

/* ------------------------------------------------------------------ wiring */

function init() {
  $('analyse').addEventListener('click', analyse);
  $('enter').addEventListener('click', enterUpdateMode);
  $('flash').addEventListener('click', flash);
  $('confirm').addEventListener('input', refreshGate);
  $('phrase').textContent = BUILD.confirmation;
  $('final-sector-label').textContent =
    `${BUILD.finalSectorStart.toLocaleString('en-US')}..${BUILD.osSize.toLocaleString('en-US')} (${(BUILD.osSize - BUILD.finalSectorStart).toLocaleString('en-US')} bytes)`;
  refreshGate();
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') init();
