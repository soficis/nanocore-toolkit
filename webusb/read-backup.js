// Nanocore firmware readback harness -- READ ONLY.
//
// What this file does: MIDI handshake into update mode (step 1), then a single
// call into the read flow of ./dfu.js over WebUSB (step 2), then hashing and
// file output in the browser.
//
// What this file deliberately does not do: it contains no USB transfer code of
// its own and never invokes the write half of the dfu.js API surface. No erase,
// no download-with-data, no manifestation. Only the read path is reachable.
//
// ./dfu.js is an unmodified copy of re-nanocore/web/dfu.js; its sha256 is
// recorded in README.md. Do not edit it.

const BOOTLOADER_SYSEX = [0xf0, 0x7d, 0x44, 0x46, 0x55, 0x01, 0xf7];
const BOOTLOADER_WAIT_MS = 1200;
const DEVICE_LABEL = '33c3:6678';

const $ = (id) => document.getElementById(id);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let dfuModule = null;
/** Loads the read API from the copied module; nothing is imported until needed. */
function loadDfuModule() {
  if (!dfuModule) dfuModule = import('./dfu.js');
  return dfuModule;
}

/* ---------------------------------------------------------------- ui helpers */

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

function setProgress(completed, total) {
  const pct = total > 0 ? Math.min(100, (completed / total) * 100) : 0;
  $('fill').style.width = pct.toFixed(1) + '%';
  $('percent').textContent = pct.toFixed(1) + '%';
  return pct;
}

function hex(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = '';
  for (let i = 0; i < bytes.length; i++) out += bytes[i].toString(16).padStart(2, '0');
  return out;
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

/* ------------------------------------------------- step 1: enter update mode */

// Sending SysEx needs the sysex permission flag in Chromium, so ask for it and
// fall back to the plain request if the user refuses.
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
      throw new Error(`${matches.length} MIDI output ports match /nanocore/i (${matches.map((p) => p.name).join(', ')}). ` +
        'Close other apps or unplug the extra unit, then reload this page.');
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
      'Sent. The pedal will disconnect and come back as an update-mode device. ' +
      'Wait a few seconds (the device name or VID:PID changes), then do step 2. ' +
      'If the browser shows a new USB device notification, that is expected.', 'ok');
  } catch (error) {
    log(`Step 1 failed: ${error.message}`);
    status('enter-status', `Step 1 failed: ${error.message}`, 'bad');
  } finally {
    button.disabled = false;
  }
}

/* -------------------------------------------- step 2: read the OS partition */

async function readFirmware() {
  const button = $('read');
  button.disabled = true;
  $('result').hidden = true;
  $('save-bin').hidden = true;
  $('save-json').hidden = true;
  setProgress(0, 1);
  status('read-status', 'Waiting for you to pick the device...');
  let transport = null;
  try {
    if (!navigator.usb) throw new Error('This browser has no WebUSB. Use desktop Chrome or Edge on the localhost URL.');
    const { WebUSBTransport, RawOS, USB_FILTER, OS_SIZE, BLOCK } = await loadDfuModule();
    $('os-size-label').textContent = OS_SIZE.toLocaleString('en-US');
    const blocks = Math.ceil(OS_SIZE / BLOCK);
    $('blocks').textContent = `0 / ${blocks} blocks`;

    const device = await navigator.usb.requestDevice({ filters: [USB_FILTER] });
    log(`Selected USB device ${device.vendorId.toString(16)}:${device.productId.toString(16)} "${device.productName || ''}".`);
    status('read-status', 'Opening the USB device...');
    transport = await WebUSBTransport.open(device);
    log('DFU interface claimed.');

    const os = new RawOS(transport);
    status('read-status', 'Reading. Do not unplug the pedal.');
    const bytes = await os.readPartition(({ completed, total }) => {
      setProgress(completed, total);
      $('blocks').textContent = `${Math.min(blocks, Math.round(completed / BLOCK))} / ${blocks} blocks`;
      status('read-status', `Reading: ${completed} of ${total} bytes. Do not unplug the pedal.`);
    });
    if (bytes.length !== OS_SIZE) throw new Error(`Read ${bytes.length} bytes, expected ${OS_SIZE}.`);

    status('read-status', 'Hashing...');
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const sha256 = hex(digest);
    const readAtUtc = new Date().toISOString();

    const receipt = {
      size: bytes.length,
      sha256,
      readAtUtc,
      userAgent: navigator.userAgent,
      osSize: OS_SIZE,
      blockSize: BLOCK,
    };
    const binBlob = new Blob([bytes], { type: 'application/octet-stream' });
    const jsonBlob = new Blob([JSON.stringify(receipt, null, 2) + '\n'], { type: 'application/json' });

    $('r-size').textContent = `${bytes.length} bytes`;
    $('r-sha').textContent = sha256;
    $('r-time').textContent = readAtUtc;
    $('result').hidden = false;
    $('save-bin').hidden = false;
    $('save-json').hidden = false;
    $('save-bin').onclick = () => saveBlob(binBlob, 'nanocore-os-readback.bin');
    $('save-json').onclick = () => saveBlob(jsonBlob, 'nanocore-os-readback.receipt.json');

    saveBlob(binBlob, 'nanocore-os-readback.bin');
    saveBlob(jsonBlob, 'nanocore-os-readback.receipt.json');
    log(`Read ${bytes.length} bytes, sha256 ${sha256}.`);
    log('Two downloads triggered. If the browser asked to allow multiple downloads, allow it.');
    status('read-status',
      'Done. Both files are downloading (nanocore-os-readback.bin and .receipt.json). ' +
      'Leave the pedal plugged in for a moment; you can unplug it once you have saved both files.', 'ok');
  } catch (error) {
    log(`Read failed: ${error.message}`);
    status('read-status', `Read failed: ${error.message}`, 'bad');
  } finally {
    if (transport) {
      try { await transport.close(); } catch (error) { log(`Close reported: ${error.message}`); }
      log('USB interface released.');
    }
    button.disabled = false;
  }
}

/* ------------------------------------------------------------------- wiring */

$('enter').addEventListener('click', enterUpdateMode);
$('read').addEventListener('click', readFirmware);

(async () => {
  try {
    const { OS_SIZE, BLOCK } = await loadDfuModule();
    $('os-size-label').textContent = OS_SIZE.toLocaleString('en-US');
    $('blocks').textContent = `0 / ${Math.ceil(OS_SIZE / BLOCK)} blocks`;
  } catch (error) {
    log(`Could not load ./dfu.js: ${error.message}. Serve this folder over http://127.0.0.1:8000, not file://.`);
  }
})();
