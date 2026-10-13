import {createHash, randomUUID} from 'node:crypto';
import {createReadStream, createWriteStream} from 'node:fs';
import {chmod, mkdir, readFile, rename, stat} from 'node:fs/promises';
import {get} from 'node:https';
import {pipeline} from 'node:stream/promises';
import {Transform} from 'node:stream';
import {fileURLToPath} from 'node:url';
import {dirname, join, resolve} from 'node:path';

export const packageRoot = dirname(fileURLToPath(import.meta.url));
const pending = new Map();
export class CliError extends Error {
  constructor(code, message, exit = 6, retryable = false, hint = 'Retry installation or check the release assets.') {
    super(message); Object.assign(this, {code, exit, retryable, hint});
  }
}
export function errorResult(error, command = 'cli') {
  const value = error instanceof CliError ? error : new CliError('ENV_NOT_READY', error.message, 3);
  return {schema_version:'1', command, status:'error', data:null, warnings:[], error:{code:value.code, message:value.message, retryable:value.retryable, hint:value.hint}};
}
export function assetName(platform = process.platform, arch = process.arch) {
  if (arch !== 'x64' || !['win32', 'linux'].includes(platform)) throw new CliError('ENV_NOT_READY', 'audio2text supports Windows/Linux x64.', 3);
  return platform === 'win32' ? 'audio2text-windows-x86_64.exe' : 'audio2text-linux-x86_64';
}
export function parseChecksum(text, asset) {
  const entries = text.split(/\r?\n/).map(line => line.trim().split(/\s+/)).filter(parts => parts[1]?.replace(/^\*/, '') === asset);
  if (entries.length !== 1 || !/^[a-f0-9]{64}$/i.test(entries[0][0])) throw new CliError('DOWNLOAD_FAILED', `Missing or ambiguous SHA256 for ${asset}`);
  return entries[0][0].toLowerCase();
}
export function response(url, redirects = 0, signal) {
  return new Promise((resolve, reject) => {
    if (new URL(url).protocol !== 'https:') return reject(new CliError('DOWNLOAD_FAILED', 'Downloads require HTTPS.'));
    const request = get(url, {signal, headers: {'User-Agent': 'audio2text-cli'}}, res => {
      if ([301, 302, 303, 307, 308].includes(res.statusCode)) {
        res.resume();
        if (redirects >= 5 || !res.headers.location) return reject(new CliError('DOWNLOAD_FAILED', 'Too many redirects.'));
        resolve(response(new URL(res.headers.location, url).href, redirects + 1, signal));
      } else if (res.statusCode !== 200) {
        res.resume(); reject(new CliError('DOWNLOAD_FAILED', `Download returned HTTP ${res.statusCode}`, 6, res.statusCode >= 500 || res.statusCode === 429));
      } else resolve(res);
    });
    request.setTimeout(120000, () => request.destroy(new CliError('DOWNLOAD_FAILED', 'Download timeout.', 6, true)));
    request.on('error', reject);
  });
}
async function checksum(path, maxBytes) {
  if ((await stat(path)).size > maxBytes) return null;
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}
async function valid(target, expected, maxBytes) {
  try {return await checksum(target, maxBytes) === expected;} catch {return false;}
}
function cancelled(signal) {
  if (signal?.aborted) throw new CliError('CANCELLED', 'Installation cancelled.', 130, true, 'Run the command again. Completed resources are retained.');
}
async function prepare(options) {
  const {root = packageRoot, platform = process.platform, arch = process.arch, download = response, retries = 3, signal, maxBytes = 100*1024*1024, delay = ms => new Promise(r => setTimeout(r, ms))} = options;
  cancelled(signal);
  const asset = assetName(platform, arch);
  let release, pkg;
  try {
    release = JSON.parse(await readFile(join(root,'release.json'),'utf8'));
    pkg = JSON.parse(await readFile(join(root,'package.json'),'utf8'));
  } catch {throw new CliError('ENV_NOT_READY','Release metadata is missing or invalid.',3,false,'Reinstall the published npm package.');}
  const repository = process.env.AUDIO2TEXT_REPOSITORY || release.repository;
  const expected = release.assets?.[asset];
  if (!/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repository || '') || release.version !== pkg.version || !/^\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?$/.test(release.version) || !/^[a-f0-9]{64}$/.test(expected || '')) {
    throw new CliError('ENV_NOT_READY','Release repository, version or checksums are invalid.',3,false,'Reinstall the published npm package.');
  }
  const target = join(root,'native',platform === 'win32' ? 'audio2text.exe' : 'audio2text');
  if (await valid(target,expected,maxBytes)) {
    if (platform !== 'win32' && !((await stat(target)).mode & 0o111)) await chmod(target,0o755);
    return target;
  }
  try {await mkdir(dirname(target),{recursive:true});} catch (error) {throw new CliError('WRITE_FAILED',error.message,7,false,'Check npm installation directory permissions.');}
  console.error(`Preparing audio2text ${release.version} for ${platform}/${arch}…`);
  const url = `https://github.com/${repository}/releases/download/v${release.version}/${asset}`;
  let last;
  for (let attempt=0; attempt<retries; attempt++) {
    cancelled(signal);
    const partial = `${target}.${process.pid}.${randomUUID()}.partial`;
    try {
      const hash = createHash('sha256'); let size=0;
      const meter = new Transform({transform(chunk,encoding,done) {
        size += chunk.length;
        if (size > maxBytes) return done(new CliError('DOWNLOAD_FAILED','Release binary exceeds the size limit.'));
        hash.update(chunk); done(null,chunk);
      }});
      const incoming = await download(url,0,signal);
      const outgoing = createWriteStream(partial,{flags:'wx'});
      outgoing.on('error',error => {if (['EACCES','EPERM','ENOSPC','EROFS','EIO'].includes(error.code)) last = new CliError('WRITE_FAILED',error.message,7,false,'Check disk space and directory permissions.');});
      await pipeline(incoming,meter,outgoing,{signal});
      cancelled(signal);
      if (hash.digest('hex') !== expected) throw new CliError('DOWNLOAD_FAILED','Release SHA256 mismatch; partial file retained.');
      await chmod(partial,0o755);
      if (await valid(target,expected,maxBytes)) return target;
      try {await rename(partial,target);} catch (error) {
        if (!await valid(target,expected,maxBytes)) throw new CliError('WRITE_FAILED',error.message,7,false,'Check disk space and directory permissions.');
      }
      return target;
    } catch (error) {
      cancelled(signal);
      last = last?.code === 'WRITE_FAILED' ? last : error instanceof CliError ? error : new CliError('DOWNLOAD_FAILED',error.message,6,true);
      if (last.exit === 7 || !last.retryable || attempt+1 === retries) throw last;
      await delay(500*(attempt+1));
    }
  }
  throw last || new CliError('DOWNLOAD_FAILED','No download attempt completed.');
}
export function ensureBinary(options = {}) {
  const key = `${resolve(options.root || packageRoot)}:${options.platform || process.platform}:${options.arch || process.arch}`;
  if (!pending.has(key)) pending.set(key,prepare(options).finally(() => pending.delete(key)));
  return pending.get(key);
}
