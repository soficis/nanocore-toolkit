// Node test for the read path of ./dfu.js, driven by a stub transport.
//
// No USB, no device, no browser: the stub answers the control requests that
// RawOS.readPartition() issues, so we can prove the read loop assembles exactly
// OS_SIZE bytes and walks the partition in BLOCK-sized steps from offset 0.
//
// Run: node test_read.js     (prints PASS or FAIL, exit code 1 on failure)

const assert = require('node:assert/strict');

// Deterministic byte for a given absolute offset, so the assembled image can be
// checked byte for byte after the read.
const byteAt = (offset) => (offset * 7 + 13) & 0xff;

class StubTransport {
  constructor() {
    this.lastAddress = null;
    this.addressRequests = []; // one per block: { command, offset, blockIndex }
    this.blockReadLengths = []; // requested byte count per block read
    this.otherRequests = [];
  }

  async transfer(type, request, value, payload = new Uint8Array(), index = 0) {
    // DFU_DNLOAD carrying an address/length payload (this is how read() seeks).
    if (type === 0x21 && request === 1) {
      assert.ok(payload instanceof Uint8Array, 'download payload must be bytes');
      assert.equal(payload.length, 5, 'address payload must be 5 bytes');
      const view = new DataView(payload.buffer, payload.byteOffset, 5);
      const command = payload[0];
      const offset = view.getUint32(1, true);
      this.lastAddress = { command, offset };
      this.addressRequests.push({ command, offset, blockIndex: value });
      return new Uint8Array();
    }

    // DFU_GETSTATUS, polled by download() until the last state is 5 (dfuDNLOAD_IDLE).
    if (type === 0xa1 && request === 3) {
      const status = new Uint8Array(6);
      status[0] = 0; // OK
      status[4] = 5; // idle, accepted
      return status;
    }

    // DFU_GETSTATE: must report 2 (dfuIDLE) for idle() to pass.
    if (type === 0xa1 && request === 5) return Uint8Array.of(2);

    // DFU_UPLOAD: hand back the block that the last address request pointed at.
    if (type === 0xa1 && request === 2) {
      assert.ok(this.lastAddress, 'upload before any address request');
      this.blockReadLengths.push(payload);
      const block = new Uint8Array(payload);
      for (let i = 0; i < block.length; i++) block[i] = byteAt(this.lastAddress.offset + i);
      return block;
    }

    this.otherRequests.push({ type, request, value, index });
    throw new Error(`Unexpected request in read-only path: type=${type} request=${request}`);
  }
}

async function main() {
  const { RawOS, OS_SIZE, BLOCK } = await import('./dfu.js');
  assert.equal(OS_SIZE, 868352, 'OS_SIZE changed');
  assert.equal(BLOCK, 4096, 'BLOCK changed');

  const total = OS_SIZE / BLOCK;
  assert.equal(total, 212, 'expected 212 blocks of 4096');

  const stub = new StubTransport();
  const progress = [];
  const os = new RawOS(stub);
  const bytes = await os.readPartition((entry) => progress.push(entry));

  // 1. exact size
  assert.equal(bytes.length, OS_SIZE, `assembled ${bytes.length} bytes, expected ${OS_SIZE}`);
  assert.ok(bytes instanceof Uint8Array, 'read must return a Uint8Array');

  // 2. one address request per block, in ascending order, BLOCK apart
  assert.equal(stub.addressRequests.length, total, 'one address request per block');
  stub.addressRequests.forEach((entry, i) => {
    assert.equal(entry.offset, i * BLOCK, `block ${i} requested offset ${entry.offset}, expected ${i * BLOCK}`);
  });

  // 3. every block read asked for exactly BLOCK bytes
  assert.equal(stub.blockReadLengths.length, total, 'one upload per block');
  assert.ok(stub.blockReadLengths.every((n) => n === BLOCK), 'all reads must be BLOCK-sized');

  // 4. read-only: the only block index used is 0, and only the seek command appears
  const blockIndexes = new Set(stub.addressRequests.map((entry) => entry.blockIndex));
  assert.deepEqual([...blockIndexes], [0], 'only block index 0 may be used on the read path');
  const commands = new Set(stub.addressRequests.map((entry) => entry.command));
  assert.equal(commands.size, 1, 'a single seek command must be used');
  for (const command of commands) {
    assert.equal(command, 0x21, `unexpected seek command ${command}`);
  }
  assert.equal(stub.otherRequests.length, 0, 'no requests outside the read path');

  // 5. content is exactly what the stub served, so nothing was shifted or dropped
  for (let i = 0; i < OS_SIZE; i++) {
    if (bytes[i] !== byteAt(i)) throw new Error(`content mismatch at byte ${i}: ${bytes[i]} != ${byteAt(i)}`);
  }

  // 6. progress was reported for every block and finished at 100%
  const final = progress[progress.length - 1];
  assert.equal(progress.length, total + 1, 'progress callback per block plus the initial 0');
  assert.equal(final.completed, OS_SIZE);
  assert.equal(final.total, OS_SIZE);
  assert.equal(final.phase, 'read');

  // 7. session still healthy (read() must not have tripped the stop flag)
  assert.equal(os.stopped, false, 'read must leave the session open');

  console.log(`blocks read: ${total}`);
  console.log(`first offsets: ${stub.addressRequests.slice(0, 4).map((e) => e.offset).join(', ')} ... ` +
    `${stub.addressRequests.slice(-1)[0].offset}`);
  console.log(`block read size: ${stub.blockReadLengths[0]}`);
  console.log(`assembled: ${bytes.length} bytes`);
}

main().then(
  () => { console.log('PASS'); },
  (error) => {
    console.error('FAIL');
    console.error(error && error.stack ? error.stack : error);
    process.exitCode = 1;
  },
);
