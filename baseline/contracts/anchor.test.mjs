import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';

const script = fileURLToPath(new URL('./anchor.mjs', import.meta.url));
const savedManifest = fileURLToPath(new URL('../results/current_demo/manifest.json', import.meta.url));

test('publishes a fresh receipt and preserves every existing output', () => {
  const folder = fs.mkdtempSync(path.join(tmpdir(), '5017-anchor-test-'));
  const manifest = path.join(folder, 'manifest.json');
  const receipt = path.join(folder, 'receipt.json');
  const symlink = path.join(folder, 'link.json');
  const run = (output) => spawnSync(process.execPath, [script, manifest, output], {encoding: 'utf8'});
  try {
    fs.copyFileSync(savedManifest, manifest);
    const original = fs.readFileSync(manifest);
    const first = run(receipt);
    assert.equal(first.status, 0, first.stderr);
    const bytes = fs.readFileSync(receipt);
    assert.equal(JSON.parse(bytes).status, 'PASS');

    fs.symlinkSync(path.join(folder, 'missing.json'), symlink);
    for (const output of [receipt, manifest, symlink]) {
      const repeated = run(output);
      assert.equal(repeated.status, 1);
      assert.match(repeated.stderr, /Output already exists/);
    }
    assert.deepEqual(fs.readFileSync(receipt), bytes);
    assert.deepEqual(fs.readFileSync(manifest), original);
    assert.ok(fs.lstatSync(symlink).isSymbolicLink());

    const blocked = run(path.join(receipt, 'invalid.json'));
    assert.equal(blocked.status, 1);
    assert.deepEqual(fs.readFileSync(receipt), bytes);
  } finally {
    fs.rmSync(folder, {recursive: true, force: true});
  }
});
