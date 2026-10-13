import {test} from 'node:test';
import assert from 'node:assert/strict';
import {assetName, parseChecksum, response, ensureBinary, errorResult} from './lib.mjs';
import {mkdir, writeFile, readFile, stat} from 'node:fs/promises';
import {createHash, randomUUID} from 'node:crypto';
import {Readable} from 'node:stream';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const bytes = Buffer.from('verified executable fixture');
const hash = createHash('sha256').update(bytes).digest('hex');
async function fixture(platform='win32') {
  const root = join(fileURLToPath(new URL('../.test-data/',import.meta.url)),`npm 中文 空格-${randomUUID()}`);
  await mkdir(root,{recursive:true});
  await writeFile(join(root,'package.json'),JSON.stringify({version:'0.1.0-beta.1'}));
  await writeFile(join(root,'release.json'),JSON.stringify({repository:'dat3133/audio2text-cli',version:'0.1.0-beta.1',assets:{[assetName(platform,'x64')]:hash}}));
  return {root,platform,arch:'x64',delay:async()=>{},download:async()=>Readable.from([bytes])};
}
test('platform names are exact and unsupported systems fail', () => {
  assert.equal(assetName('win32','x64'),'audio2text-windows-x86_64.exe');
  assert.equal(assetName('linux','x64'),'audio2text-linux-x86_64');
  assert.throws(() => assetName('darwin','arm64'));
});
test('checksums are selected by exact asset name', () => {
  const hash = 'a'.repeat(64);
  assert.equal(parseChecksum(`${hash}  tool.exe\n${'b'.repeat(64)}  other`, 'tool.exe'),hash);
  assert.throws(() => parseChecksum(`${hash} tool.exe.evil`, 'tool.exe'));
  assert.throws(() => parseChecksum(`${hash} tool.exe\n${hash} tool.exe`, 'tool.exe'));
});
test('HTTP downloads are rejected', async () => {await assert.rejects(response('http://example.com/file'));});
for (const platform of ['win32','linux']) test(`verified ${platform} cache works offline with unicode paths`,async()=>{
  const options = await fixture(platform);
  let calls=0;
  options.download = async url => {assert.match(url,/v0\.1\.0-beta\.1/);calls++;return Readable.from([bytes]);};
  const target = await ensureBinary(options);
  assert.deepEqual(await readFile(target),bytes);
  const before = (await stat(target)).mtimeMs;
  options.download = async()=>{throw new Error('must remain offline');};
  assert.equal(await ensureBinary(options),target);
  assert.equal((await stat(target)).mtimeMs,before);
  assert.equal(calls,1);
});
test('corrupt cache is repaired and same-process requests share a download',async()=>{
  const options = await fixture();
  await mkdir(join(options.root,'native'));
  await writeFile(join(options.root,'native','audio2text.exe'),'old broken binary');
  let calls=0;
  options.download = async()=>{calls++;return Readable.from([bytes]);};
  const targets = await Promise.all(Array.from({length:8},()=>ensureBinary(options)));
  assert.equal(new Set(targets).size,1);assert.equal(calls,1);
  assert.deepEqual(await readFile(targets[0]),bytes);
});
test('independent installers commit the same verified executable concurrently',async()=>{
  const options = await fixture();
  const other = await import(`./lib.mjs?installer=${randomUUID()}`);
  const targets = await Promise.all([ensureBinary(options),other.ensureBinary(options)]);
  assert.equal(targets[0],targets[1]);assert.deepEqual(await readFile(targets[0]),bytes);
});
test('interrupted streams retry with unique partial files',async()=>{
  const options = await fixture();let calls=0;
  options.download = async()=>{
    calls++;
    if (calls===1) return Readable.from((async function*(){yield Buffer.from('partial');throw new Error('connection interrupted');})());
    return Readable.from([bytes]);
  };
  assert.deepEqual(await readFile(await ensureBinary(options)),bytes);assert.equal(calls,2);
});
test('network retry count is bounded and reports a retryable JSON error',async()=>{
  const options=await fixture();let calls=0;
  options.download=async()=>{calls++;throw new Error('offline');};
  await assert.rejects(ensureBinary(options),error=>{
    assert.equal(calls,3);assert.equal(error.exit,6);
    assert.equal(errorResult(error,'doctor').error.retryable,true);return true;
  });
});
test('bad checksum or oversized payload never becomes an executable',async()=>{
  for (const oversized of [false,true]) {
    const options=await fixture();
    options.download=async()=>Readable.from([Buffer.from('incorrect executable')]);
    if(oversized) options.maxBytes=4;
    await assert.rejects(ensureBinary(options),error=>error.code==='DOWNLOAD_FAILED' && error.exit===6);
    await assert.rejects(stat(join(options.root,'native','audio2text.exe')));
  }
});
test('metadata mismatch fails before accessing the network',async()=>{
  const options=await fixture();
  await writeFile(join(options.root,'package.json'),JSON.stringify({version:'0.1.0'}));
  options.download=async()=>{assert.fail('network must not be accessed');};
  await assert.rejects(ensureBinary(options),error=>error.code==='ENV_NOT_READY' && error.exit===3);
});
test('cancellation preserves the cache and returns exit 130',async()=>{
  const options=await fixture();const controller=new AbortController();
  options.signal=controller.signal;
  options.download=async()=>{controller.abort();throw new Error('aborted');};
  await assert.rejects(ensureBinary(options),error=>error.code==='CANCELLED' && error.exit===130);
});
