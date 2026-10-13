import {ensureBinary} from './lib.mjs';
try {
  await ensureBinary();
  console.error('audio2text CLI installed. Run: audio2text init');
} catch (error) {
  console.error(`audio2text installation failed: ${error.message}`);
  process.exitCode = 1;
}
