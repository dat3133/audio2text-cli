import {createHash} from 'node:crypto';
import {createWriteStream} from 'node:fs';
import {access, chmod, mkdir, readFile, rename} from 'node:fs/promises';
import {get} from 'node:https';
import {pipeline} from 'node:stream/promises';
import {Transform} from 'node:stream';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

export const packageRoot = dirname(fileURLToPath(import.meta.url));
export function assetName(platform = process.platform, arch = process.arch) {
  if (arch !== 'x64' || !['win32', 'linux'].includes(platform)) throw new Error('audio2text supports Windows/Linux x64.');
  return platform === 'win32' ? 'audio2text-windows-x86_64.exe' : 'audio2text-linux-x86_64';
}
export function parseChecksum(text, asset) {
  const entry = text.split(/\r?\n/).map(line => line.trim().split(/\s+/)).find(parts => parts[1]?.replace(/^\*/, '') === asset);
  if (!entry || !/^[a-f0-9]{64}$/i.test(entry[0])) throw new Error(`Missing SHA256 for ${asset}`);
  return entry[0].toLowerCase();
}
export function response(url, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (new URL(url).protocol !== 'https:') return reject(new Error('Downloads require HTTPS.'));
    const request = get(url, {headers: {'User-Agent': 'audio2text-cli'}}, res => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
        res.resume();
        if (redirects >= 5 || !res.headers.location) return reject(new Error('Too many redirects.'));
        resolve(response(new URL(res.headers.location, url).href, redirects + 1));
      } else if (res.statusCode !== 200) {
        res.resume(); reject(new Error(`Download returned HTTP ${res.statusCode}`));
      } else resolve(res);
    });
    request.setTimeout(120000, () => request.destroy(new Error('Download timeout.')));
    request.on('error', reject);
  });
}
async function metadata() {
  try {return JSON.parse(await readFile(join(packageRoot, 'release.json'), 'utf8'));}
  catch {return {};}
}
export async function ensureBinary() {
  const asset = assetName();
  const target = join(packageRoot, 'native', process.platform === 'win32' ? 'audio2text.exe' : 'audio2text');
  try {await access(target); return target;} catch {}
  const release = await metadata();
  const repository = process.env.AUDIO2TEXT_REPOSITORY || release.repository;
  if (!/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repository || '')) throw new Error('Release repository is not configured. Build a local npm package with scripts/prepare-npm.mjs, or set AUDIO2TEXT_REPOSITORY=dat3133/audio2text-cli.');
  const version = release.version || JSON.parse(await readFile(join(packageRoot, 'package.json'), 'utf8')).version;
  if (!/^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$/.test(version)) throw new Error('Invalid release version.');
  const base = `https://github.com/${repository}/releases/download/v${version}`;
  const checksumResponse = await response(`${base}/SHA256SUMS`);
  let checksums = '';
  for await (const data of checksumResponse) {checksums += data; if (checksums.length > 65536) throw new Error('Checksum manifest too large.');}
  const expected = parseChecksum(checksums, asset);
  await mkdir(dirname(target), {recursive: true});
  const partial = `${target}.${process.pid}.partial`;
  const hash = createHash('sha256');
  let size = 0;
  const meter = new Transform({transform(chunk, encoding, done) {
    size += chunk.length;
    if (size > 100 * 1024 * 1024) return done(new Error('Release binary too large.'));
    hash.update(chunk); done(null, chunk);
  }});
  await pipeline(await response(`${base}/${asset}`), meter, createWriteStream(partial, {flags: 'wx'}));
  if (hash.digest('hex') !== expected) throw new Error('Release SHA256 mismatch; incomplete file retained for inspection.');
  await chmod(partial, 0o755);
  await rename(partial, target);
  return target;
}
