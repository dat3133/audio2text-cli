#!/usr/bin/env node
import {spawn} from 'node:child_process';
import {ensureBinary} from '../lib.mjs';
try {
  const binary = await ensureBinary();
  const child = spawn(binary, process.argv.slice(2), {stdio: 'inherit', windowsHide: true});
  child.on('error', error => {console.error(error.message); process.exitCode = 1;});
  child.on('exit', (code, signal) => {process.exitCode = signal === 'SIGINT' ? 130 : (code ?? 1);});
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
} catch (error) {
  console.log(JSON.stringify({schema_version:'1',command:'cli',status:'error',data:{},warnings:[],error:{code:'ENV_NOT_READY',message:error.message,retryable:false,hint:'Reinstall the CLI package.'}}));
  process.exitCode = 3;
}
