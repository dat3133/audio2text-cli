import {ensureBinary} from './lib.mjs';
const controller = new AbortController();
process.on('SIGINT',() => controller.abort());
try {
  await ensureBinary({signal:controller.signal});
  console.error('audio2text CLI installed. Run: audio2text init');
} catch (error) {
  console.error(`audio2text installation failed: ${error.message}`);
  process.exitCode = error.exit || 6;
}
