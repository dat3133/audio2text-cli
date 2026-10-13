import {test} from 'node:test';
import assert from 'node:assert/strict';
import {assetName, parseChecksum, response} from './lib.mjs';
test('platform names are exact and unsupported systems fail', () => {
  assert.equal(assetName('win32','x64'),'audio2text-windows-x86_64.exe');
  assert.equal(assetName('linux','x64'),'audio2text-linux-x86_64');
  assert.throws(() => assetName('darwin','arm64'));
});
test('checksums are selected by exact asset name', () => {
  const hash = 'a'.repeat(64);
  assert.equal(parseChecksum(`${hash}  tool.exe\n${'b'.repeat(64)}  other`, 'tool.exe'),hash);
  assert.throws(() => parseChecksum(`${hash} tool.exe.evil`, 'tool.exe'));
});
test('HTTP downloads are rejected', async () => {await assert.rejects(response('http://example.com/file'));});
