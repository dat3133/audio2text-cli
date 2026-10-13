import {mkdir, readFile, writeFile, copyFile, readdir} from 'node:fs/promises';
import {join, resolve} from 'node:path';
import {parseChecksum} from '../npm/lib.mjs';
const args = process.argv.slice(2);
const allowed = new Set(['--repository','--checksums','--output-dir','--notices']);
const values = new Map();
for (let i=0;i<args.length;i+=2) {
  if (!allowed.has(args[i]) || !args[i+1] || args[i+1].startsWith('--') || values.has(args[i])) throw new Error('Expected --checksums <file> --output-dir <fresh directory> [--repository owner/repo]. Embedded binary packaging is unsupported.');
  values.set(args[i],args[i+1]);
}
const repository = values.get('--repository') || process.env.GITHUB_REPOSITORY || 'dat3133/audio2text-cli';
if (!/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repository)) throw new Error('Expected owner/repository');
if (!values.has('--checksums') || !values.has('--output-dir')) throw new Error('--checksums and --output-dir are required.');
const output = resolve(values.get('--output-dir'));
await mkdir(output,{recursive:true});
if ((await readdir(output)).length) throw new Error('Package output directory must be empty.');
const pkg = JSON.parse(await readFile('npm/package.json','utf8'));
const sums = await readFile(values.get('--checksums'),'utf8');
const assets = Object.fromEntries(['audio2text-windows-x86_64.exe','audio2text-linux-x86_64'].map(name => [name,parseChecksum(sums,name)]));
await mkdir(join(output,'bin'));
for (const name of ['bin/audio2text.mjs','install.mjs','lib.mjs','package.json']) await copyFile(join('npm',name),join(output,name));
await writeFile(join(output,'release.json'),JSON.stringify({repository,version:pkg.version,assets},null,2)+'\n');
const readme = (await readFile('README.md','utf8')).replace(/\]\((?![a-z]+:|#)([^)]+)\)/g,(_,target) => `](https://github.com/${repository}/blob/v${pkg.version}/${target})`);
await writeFile(join(output,'README.md'),readme);
await copyFile('LICENSE',join(output,'LICENSE'));
await copyFile(values.get('--notices') || 'THIRD_PARTY_NOTICES.md',join(output,'THIRD_PARTY_NOTICES.md'));
console.log(`Prepared platform-neutral npm package: ${output}`);
