import {mkdir, readFile, writeFile, copyFile} from 'node:fs/promises';
import {join, resolve} from 'node:path';
const args = process.argv.slice(2);
const value = key => {const i=args.indexOf(key);return i>=0 ? args[i+1] : undefined;};
const repository = value('--repository') || process.env.GITHUB_REPOSITORY || 'dat3133/audio2text-cli';
if (repository && !/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repository)) throw new Error('Expected owner/repository');
const pkg=JSON.parse(await readFile('npm/package.json','utf8'));
await writeFile('npm/release.json',JSON.stringify({repository,version:pkg.version},null,2)+'\n');
await copyFile('README.md','npm/README.md');
await copyFile('LICENSE','npm/LICENSE');
const binary=value('--binary');
if(binary) {
  await mkdir('npm/native',{recursive:true});
  await copyFile(resolve(binary),join('npm/native',process.platform==='win32'?'audio2text.exe':'audio2text'));
}
console.log('npm package prepared. Run npm pack in npm/; initialization never runs during postinstall.');
