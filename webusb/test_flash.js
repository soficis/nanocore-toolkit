// Node test for the flash path. No USB, no browser, no device.
//
// It loads the two REAL images and the REAL descriptor, imports the local
// firmware-lite.js validator and the page's own planning/assertion functions
// from flash.js, and proves:
//   * the patched image authenticates as "installed" and the readback as "stock";
//   * the erase plan is exactly the four sectors the offline builder predicted,
//     with the final partial sector (851,968..868,352) excluded;
//   * a target with one changed byte inside that final partial sector is
//     REJECTED before a single transfer is attempted.
//
// Run: node test_flash.js --stock <stock-readback.bin> --target <patched-os.bin> --patch <patch.json>
//      (prints PASS/FAIL per check, exit 1 on failure, exit 2 if a file is missing)

const assert = require('node:assert/strict');
const fs = require('node:fs');

// No firmware images ship with this repo. Point the test at your own build. The
// hashes it asserts against are the ones pinned in BUILD inside flash.js, so a
// failure here means your files are not the build that page was pinned to.
function argValue(flag) {
  const index = process.argv.indexOf(flag);
  return index > -1 ? process.argv[index + 1] : undefined;
}

const PATHS = {
  stock: argValue('--stock') || process.env.NANOCORE_STOCK,
  target: argValue('--target') || process.env.NANOCORE_TARGET,
  patch: argValue('--patch') || process.env.NANOCORE_PATCH,
};

for (const [label, file] of Object.entries(PATHS)) {
  if (!file) {
    console.error(`error: no ${label} file given. No firmware images are distributed with this repo.`);
    console.error('usage: node test_flash.js --stock <stock-readback.bin> --target <patched-os.bin> --patch <patch.json>');
    process.exit(2);
  }
  if (!fs.existsSync(file)) {
    console.error(`error: ${label} file not found: ${file}`);
    process.exit(2);
  }
}

const results = [];
function check(label, fn) {
  try {
    fn();
    results.push({ label, ok: true });
    console.log(`PASS  ${label}`);
  } catch (error) {
    results.push({ label, ok: false, error });
    console.log(`FAIL  ${label}`);
    console.log(`      ${error && error.message ? error.message : error}`);
  }
}

async function checkAsync(label, fn) {
  try {
    await fn();
    results.push({ label, ok: true });
    console.log(`PASS  ${label}`);
  } catch (error) {
    results.push({ label, ok: false, error });
    console.log(`FAIL  ${label}`);
    console.log(`      ${error && error.message ? error.message : error}`);
  }
}

/** Any transfer at all is a failure: the rejection must happen before USB work. */
class ForbiddenTransport {
  constructor() {
    this.transfers = 0;
  }

  async transfer(type, request) {
    this.transfers += 1;
    throw new Error(`USB transfer attempted (type=${type} request=${request}); this must never happen in the test`);
  }
}

async function main() {
  const stock = new Uint8Array(fs.readFileSync(PATHS.stock));
  const target = new Uint8Array(fs.readFileSync(PATHS.target));
  const descriptorRaw = new Uint8Array(fs.readFileSync(PATHS.patch));
  const patch = JSON.parse(Buffer.from(descriptorRaw).toString('utf8'));

  const { validateImage } = await import('./firmware-lite.js');
  const flash = await import('./flash.js');
  const { RawOS, OS_SIZE, SECTOR } = await import('./dfu.js');
  const { BUILD, planSectors, inspect, assertFlashPreconditions, sha256Hex } = flash;

  console.log(`stock   ${stock.length} B`);
  console.log(`target  ${target.length} B`);
  console.log(`patch   ${descriptorRaw.length} B, firmwareVersion ${patch.firmwareVersion}`);
  console.log('');

  // ---- shape ---------------------------------------------------------------
  check('dfu.js OS_SIZE and SECTOR are unchanged (868352 / 65536)',
    () => { assert.equal(OS_SIZE, 868352); assert.equal(SECTOR, 65536); });
  check('stock readback is 868,352 bytes', () => assert.equal(stock.length, OS_SIZE));
  check('patched image is 868,352 bytes', () => assert.equal(target.length, OS_SIZE));
  check('build constants use the same OS_SIZE', () => assert.equal(BUILD.osSize, OS_SIZE));

  // ---- descriptor identity -------------------------------------------------
  await checkAsync('descriptor sha256 matches the recorded manifest',
    async () => assert.equal(await sha256Hex(descriptorRaw), BUILD.descriptorSha256));
  check('descriptor declares firmware 1.2 and osSize 868352',
    () => { assert.equal(patch.firmwareVersion, '1.2'); assert.equal(patch.osSize, 868352); });

  // ---- image authentication (firmware-lite.js) ----------------------------
  await checkAsync('validateImage(stock).kind === "stock"',
    async () => assert.equal((await validateImage(stock, patch)).kind, 'stock'));
  await checkAsync('validateImage(target).kind === "installed"',
    async () => assert.equal((await validateImage(target, patch)).kind, 'installed'));

  // ---- erase plan ----------------------------------------------------------
  const sectors = planSectors(stock, target);
  check('planned erase sectors are exactly 0x000000, 0x040000, 0x080000, 0x0a0000',
    () => assert.deepEqual(sectors, [0x000000, 0x040000, 0x080000, 0x0a0000]));
  check('sector plan is identical to BUILD.expectedSectors',
    () => assert.deepEqual(sectors, BUILD.expectedSectors));
  check('the final partial sector 851,968..868,352 is excluded from the plan',
    () => { assert.equal(sectors.includes(851968), false); assert.ok(sectors.every((o) => o + SECTOR <= OS_SIZE)); });
  check('the final partial region is byte-identical between the two images',
    () => { for (let i = 851968; i < OS_SIZE; i++) assert.equal(stock[i], target[i], `byte ${i} differs`); });
  check('erased bytes total 4 x 65,536 = 262,144', () => assert.equal(sectors.length * SECTOR, 262144));

  // ---- Stage 0 report (the page's own gate) -------------------------------
  const report = await inspect({ stock, target, patch });
  check('Stage 0 inspect() passes on the real files',
    () => { assert.equal(report.ok, true, report.rows.filter((r) => !r.ok).map((r) => r.label).join('; ')); });
  check('Stage 0 inspect() plans the same four sectors',
    () => assert.deepEqual(report.sectors, BUILD.expectedSectors));
  check('Stage 0 reports the final partial sector untouched', () => assert.equal(report.finalSectorUntouched, true));

  // ---- last gate -----------------------------------------------------------
  await checkAsync('assertFlashPreconditions passes on the real pair',
    async () => {
      const gate = await assertFlashPreconditions({ expectedCurrent: stock, target, patch });
      assert.equal(gate.targetSha, BUILD.targetSha256);
      assert.equal(gate.stockSha, BUILD.stockSha256);
      assert.equal(gate.targetState.kind, 'installed');
      assert.equal(gate.stockState.kind, 'stock');
    });

  // ---- rejection before any transfer --------------------------------------
  const tampered = target.slice();
  tampered[851968 + 100] ^= 0xff; // one byte inside the final partial sector
  check('tampered target really differs from target',
    () => assert.notEqual(tampered[851968 + 100], target[851968 + 100]));

  const stub = new ForbiddenTransport();
  const os = new RawOS(stub);
  let rejection = null;
  await checkAsync('program() rejects a target changed inside 851,968..868,352',
    async () => {
      await assert.rejects(
        () => os.program({ expectedCurrent: stock, target: tampered, backupSaved: () => true, onVerified: () => {} }),
        (error) => { rejection = error; return /Changed final partial erase sector/i.test(error.message); },
      );
    });
  check('the rejection is the final-partial-sector guard',
    () => assert.match(rejection.message, /Changed final partial erase sector/i));
  check('zero USB transfers were attempted before the rejection', () => assert.equal(stub.transfers, 0));
  check('the DFU session stopped instead of continuing', () => assert.equal(os.stopped, true));

  await checkAsync('assertFlashPreconditions also rejects the tampered target',
    async () => {
      await assert.rejects(
        () => assertFlashPreconditions({ expectedCurrent: stock, target: tampered, patch }),
        /is not the recorded build|not the recorded build/i,
      );
    });

  // ---- summary -------------------------------------------------------------
  const failed = results.filter((r) => !r.ok);
  console.log('');
  console.log(`${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) {
    console.log('\nFAIL');
    process.exitCode = 1;
  } else {
    console.log('PASS');
  }
}

main().catch((error) => {
  console.error('FAIL');
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
