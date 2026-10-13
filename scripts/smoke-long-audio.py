"""Opt-in long-running validation; generated speech/outputs stay outside source."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("--binary", required=True)
parser.add_argument("--data-dir", required=True)
parser.add_argument("--ffmpeg", required=True)
parser.add_argument("--fixture", required=True)
parser.add_argument("--seconds", type=int, default=7200)
parser.add_argument("--report-dir", default=".test-data")
args = parser.parse_args()
if args.seconds < 1:
    parser.error("seconds must be positive")
root = Path(args.report_dir).resolve() / ("long-smoke-" + uuid.uuid4().hex)
root.mkdir(parents=True)
audio, transcript = root / "two-hours.flac", root / "transcript.txt"
subprocess.run([args.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-stream_loop", "-1", "-i", str(Path(args.fixture).resolve()), "-t", str(args.seconds), "-ac", "1", "-ar", "16000", "-c:a", "flac", str(audio)], check=True)
print(f"Fixture prepared: {audio}", flush=True)
started = time.monotonic()
with (root / "progress.log").open("w", encoding="utf-8") as log:
    process = subprocess.run([args.binary, "transcribe", str(audio), "--data-dir", args.data_dir, "--output", str(transcript), "--no-text", "--language", "en"], stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8")
value = json.loads(process.stdout)
report = {"fixture": str(audio), "fixture_source": "Repeated locally generated SAPI speech; no source audio redistributed", "wall_seconds": round(time.monotonic() - started, 3), "exit": process.returncode, "result": value}
(root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
if process.returncode or value["status"] != "ok":
    raise SystemExit(f"Long-audio validation failed: {root / 'report.json'}")
result = value["data"]["results"][0]
if abs(result["audio_duration_seconds"] - args.seconds) > 0.1:
    raise SystemExit("Duration mismatch")
if not transcript.read_text(encoding="utf-8").strip():
    raise SystemExit("Empty transcript")
print(json.dumps({"report":str(root / "report.json"),"duration_seconds":result["audio_duration_seconds"],"wall_seconds":report["wall_seconds"],"peak_worker_rss_bytes":value["data"].get("peak_worker_rss_bytes")},ensure_ascii=False))
