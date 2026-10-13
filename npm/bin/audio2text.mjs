#!/usr/bin/env node
import {spawn} from 'node:child_process';
import {ensureBinary, errorResult, CliError} from '../lib.mjs';
const controller = new AbortController();
let child, reported = false;
const command = process.argv.slice(2).find(value => ['init','doctor','models','transcribe'].includes(value)) || 'cli';
function report(error) {
  if (reported) return;
  reported = true;
  console.log(JSON.stringify(errorResult(error,command)));
  process.exitCode = error.exit || 3;
}
for (const signal of ['SIGINT','SIGTERM']) process.on(signal,() => {if (child) child.kill(signal); else controller.abort();});
try {
  const binary = await ensureBinary({signal:controller.signal});
  child = spawn(binary,process.argv.slice(2),{stdio:'inherit',windowsHide:true});
  child.on('error',error => report(new CliError('ENV_NOT_READY',error.message,3,false,'Check executable permissions or reinstall the CLI.')));
  child.on('exit',(code,signal) => {if (!reported) process.exitCode = signal === 'SIGINT' ? 130 : signal === 'SIGTERM' ? 143 : (code ?? 1);});
} catch (error) {
  report(error);
}
