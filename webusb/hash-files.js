#!/usr/bin/env node
// Compute the four `BUILD` hashes and the erase plan for flash.html.
//
// The flash page refuses to run unless its hard-coded BUILD constants match the
// files you load. That is deliberate: program() in dfu.js authenticates nothing,
// so those constants are the only thing standing between a wrong image and an
// erased partition. This script prints the values to paste in when you pin the
// page to your own build.
//
// Usage:
//   node hash-files.js --target <patched-os.bin> --stock <stock-readback.bin> \
//                      --descriptor <patch.json> [--fit-size 759808]
//
// CommonJS on purpose: this directory has no package.json, so it runs as-is.

'use strict';

const fs = require('node:fs');
const crypto = require('node:crypto');
const path = require('node:path');

const OS_SIZE = 868352;
const SECTOR = 65536;
const FINAL_SECTOR_START = 851968;
const DEFAULT_FIT_SIZE = 759808;

function usage(message) {
  if (message) console.error(`error: ${message}\n`);
  console.error('usage: node hash-files.js --target <patched-os.bin> --stock <stock-readback.bin> \\');
  console.error('                         --descriptor <patch.json> [--fit-size 759808]');
  process.exit(2);
}

function parseArgs(argv) {
  const out = { fitSize: DEFAULT_FIT_SIZE };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    const value = argv[i + 1];
    switch (flag) {
      case '--target': out.target = value; i += 1; break;
      case '--stock': out.stock = value; i += 1; break;
      case '--descriptor': out.descriptor = value; i += 1; break;
      case '--fit-size': out.fitSize = Number(value); i += 1; break;
      case '-h':
      case '--help': usage(); break;
      default: usage(`unknown argument ${flag}`);
    }
  }
  if (!out.target) usage('--target is required');
  if (!out.stock) usage('--stock is required');
  if (!out.descriptor) usage('--descriptor is required');
  if (!Number.isSafeInteger(out.fitSize) || out.fitSize <= 0) usage('--fit-size must be a positive integer');
  return out;
}

const sha256 = (buffer) => crypto.createHash('sha256').update(buffer).digest('hex');

function planSectors(expected, target) {
  if (expected.length !== target.length) {
    throw new Error(`length mismatch: ${expected.length} vs ${target.length}`);
  }
  const sectors = [];
  for (let offset = 0; offset < expected.length; offset += SECTOR) {
    const a = expected.subarray(offset, offset + SECTOR);
    const b = target.subarray(offset, offset + SECTOR);
    if (!a.equals(b)) sectors.push(offset);
  }
  return sectors;
}

function load(file, label) {
  if (!fs.existsSync(file)) usage(`${label} not found: ${file}`);
  const buffer = fs.readFileSync(file);
  if (buffer.length !== OS_SIZE) {
    usage(`${label} is ${buffer.length} bytes, expected ${OS_SIZE} for a full OS partition: ${file}`);
  }
  return buffer;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const target = load(args.target, '--target');
  const stock = load(args.stock, '--stock');

  if (args.fitSize > OS_SIZE) usage(`--fit-size ${args.fitSize} exceeds the ${OS_SIZE}-byte partition`);

  const descriptorRaw = fs.readFileSync(args.descriptor);
  const descriptor = JSON.parse(descriptorRaw.toString('utf8'));

  const sectors = planSectors(stock, target);

  for (const offset of sectors) {
    if (offset + SECTOR > FINAL_SECTOR_START) {
      usage(`sector 0x${offset.toString(16)} would cross ${FINAL_SECTOR_START}; ` +
        'the final partial sector holds device and preset data and must never be erased');
    }
  }

  const changed = sectors.reduce((total, offset) => {
    const a = stock.subarray(offset, offset + SECTOR);
    const b = target.subarray(offset, offset + SECTOR);
    let count = 0;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) count += 1;
    return total + count;
  }, 0);

  const hex = (n) => `0x${n.toString(16).padStart(6, '0')}`;

  console.log('BUILD constants for webusb/flash.js');
  console.log('');
  console.log(`  osSize:            ${OS_SIZE}`);
  console.log(`  fitSize:           ${args.fitSize}`);
  console.log(`  sector:            ${SECTOR}`);
  console.log(`  finalSectorStart:  ${FINAL_SECTOR_START}`);
  console.log(`  targetSha256:      '${sha256(target)}'`);
  console.log(`  targetFitSha256:   '${sha256(target.subarray(0, args.fitSize))}'`);
  console.log(`  stockSha256:       '${sha256(stock)}'`);
  console.log(`  descriptorSha256:  '${sha256(descriptorRaw)}'`);
  console.log(`  expectedSectors:   [${sectors.map(hex).join(', ')}]`);
  console.log('');
  console.log('Erase plan');
  console.log('');
  console.log(`  sectors to erase:  ${sectors.length} (${sectors.length * SECTOR} bytes)`);
  console.log(`  bytes that differ: ${changed}`);
  for (const offset of sectors) {
    const a = stock.subarray(offset, offset + SECTOR);
    const b = target.subarray(offset, offset + SECTOR);
    let count = 0;
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) count += 1;
    console.log(`    ${hex(offset)}..${hex(offset + SECTOR - 1)}  ${String(count).padStart(5)} changed`);
  }
  const covered = new Set(sectors);
  let outside = 0;
  for (let offset = 0; offset < OS_SIZE; offset += SECTOR) {
    if (covered.has(offset)) continue;
    if (!stock.subarray(offset, offset + SECTOR).equals(target.subarray(offset, offset + SECTOR))) outside += 1;
  }
  console.log(`  changed bytes outside the erase plan: ${outside === 0 ? '0 (good)' : `${outside} sectors — STOP`}`);
  console.log('');
  console.log(`Descriptor: ${path.basename(args.descriptor)} — firmwareVersion=${descriptor.firmwareVersion || '(unset)'}, ` +
    `effect=${descriptor.effect || '(unset)'}, replaces=${descriptor.replaces || '(unset)'}, ` +
    `patches=${Array.isArray(descriptor.patches) ? descriptor.patches.length : '(unset)'}`);
  console.log('');
  console.log('Paste the values above into BUILD in flash.js, then run: node test_flash.js');
}

main();
