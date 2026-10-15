"""Install the skill with pinned Skills CLI in isolated profiles; no ASR setup."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from release_common import ROOT, npm_command

SKILLS_VERSION = "1.7.2"
DISPLAY_NAMES = {"Codex", "Claude Code"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(command, cwd, env):
    result = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", timeout=180)
    require(result.returncode == 0,
            f"Command failed in {cwd}: {command}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def isolated_env(case, cache):
    profile = case / "profile"
    project = case / "project"
    temporary = case / "tmp"
    # Skills list reports ownership only for detected agents. Empty agent roots
    # represent installed hosts without depending on real Codex/Claude profiles.
    for path in (profile / ".codex", profile / ".claude", project, temporary):
        path.mkdir(parents=True)
    env = os.environ.copy()
    # os.homedir(), agent overrides and npm must all avoid the real user profile.
    env.update({
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "CODEX_HOME": str(profile / ".codex"),
        "CLAUDE_CONFIG_DIR": str(profile / ".claude"),
        "XDG_CONFIG_HOME": str(profile / ".config"),
        "XDG_DATA_HOME": str(profile / ".local/share"),
        "XDG_CACHE_HOME": str(profile / ".cache"),
        "APPDATA": str(profile / "AppData/Roaming"),
        "LOCALAPPDATA": str(profile / "AppData/Local"),
        "NPM_CONFIG_USERCONFIG": str(profile / ".npmrc"),
        "NPM_CONFIG_CACHE": str(cache),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "AUDIO2TEXT_HOME": str(case / "unused-model-home"),
        "DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "NO_COLOR": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env, profile, project


def check_content(folder, expected, case):
    require(folder.resolve().is_relative_to(case), f"Installation escaped isolation: {folder}")
    require({p.name for p in folder.iterdir()} == {"SKILL.md"},
            f"Expected a self-contained, single-file skill: {folder}")
    content = (folder / "SKILL.md").read_text("utf-8")
    require(content == expected, f"Installed instructions differ from checkout: {folder}")
    # Relative Markdown resources must travel with the installed skill.
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
        if target.startswith(("https://", "http://", "#")):
            continue
        resource = (folder / target.split("#", 1)[0]).resolve()
        require(resource.is_relative_to(folder.resolve()) and resource.exists(),
                f"Installed skill depends on an unavailable resource: {target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(ROOT),
                        help="Default: checked-out source. Use the public GitHub URL after pushing.")
    args = parser.parse_args()
    require(shutil.which("git") is not None, "Skills installation requires Git")
    node = shutil.which("node")
    require(node is not None, "Skills CLI requires Node.js 22.20+")
    node_version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    require(tuple(map(int, node_version.split("."))) >= (22, 20, 0),
            f"Skills CLI requires Node.js 22.20+; found {node_version}")
    expected = (ROOT / "skills/audio2text/SKILL.md").read_text("utf-8")
    source = str(Path(args.source).resolve()) if Path(args.source).exists() else args.source
    base = ROOT / ".test-data"
    base.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="skill 中文 空格-", dir=base))
    command = npm_command("exec", "--yes", f"--package=skills@{SKILLS_VERSION}") + ["--", "skills"]
    print(f"Skills {SKILLS_VERSION}: {source}\nIsolated fixtures: {work}", flush=True)
    for scope in ("project", "global"):
        for mode in ("default", "copy"):
            case = work / f"{scope}-{mode}"
            env, profile, project = isolated_env(case, work / "npm-cache")
            require(run([*command, "--version"], project, env) == SKILLS_VERSION,
                    "Unexpected Skills CLI version")
            scope_flags = ["--global"] if scope == "global" else []
            result = json.loads(run([
                *command, "add", source, "--skill", "audio2text", "--agent", "codex", "claude-code",
                "--yes", "--json", *scope_flags, *(["--copy"] if mode == "copy" else []),
            ], project, env))
            require(len(result) == 1, f"Unexpected install results: {result}")
            item = result[0]
            require(item["name"] == "audio2text" and item["status"] == "installed"
                    and item["scope"] == scope and set(item["agents"]) == DISPLAY_NAMES,
                    f"Wrong skill, scope or agents: {item}")
            require(item["mode"] == ("copy" if mode == "copy" else "symlink"),
                    f"Wrong installation mode: {item}")
            root = profile if scope == "global" else project
            canonical = root / ".agents/skills/audio2text"
            claude = root / ".claude/skills/audio2text"
            for folder in (canonical, claude, Path(item["path"])):
                check_content(folder, expected, case)
            if mode == "copy":
                require(not claude.is_symlink(), "Copy mode unexpectedly created a symlink")
                require(claude.resolve() != canonical.resolve(), "Copy mode shares agent files")
            else:
                # Windows may legitimately fall back to a copy without symlink permission.
                require(claude.resolve() in (claude, canonical.resolve()), "Link points outside installed skill")
            listed = json.loads(run([*command, "list", "--json", "--agent", "codex", "claude-code",
                                     *scope_flags], project, env))
            require(len(listed) == 1 and listed[0]["name"] == "audio2text"
                    and listed[0]["scope"] == scope
                    and set(listed[0]["agents"]) == DISPLAY_NAMES,
                    f"Installed skill is not discoverable by both agents: {listed}")
            opposite_flags = [] if scope == "global" else ["--global"]
            require(json.loads(run([*command, "list", "--json", "--agent", "codex", "claude-code",
                                    *opposite_flags], project, env)) == [],
                    "Skill leaked into the other scope")
            for path in (Path(env["AUDIO2TEXT_HOME"]), Path(env["LOCALAPPDATA"]) / "audio2text",
                         Path(env["XDG_DATA_HOME"]) / "audio2text"):
                require(not path.exists(), f"Skill installation prepared ASR resources: {path}")
            link_mode = "linked" if claude.resolve() == canonical.resolve() else "copied"
            print(f"PASS {scope}/{mode}: audio2text; Codex + Claude Code; {link_mode}; no ASR downloads",
                  flush=True)
    print("All four isolated installation scenarios passed.")


if __name__ == "__main__":
    # Hosted Windows pipes can default to cp1252; fixtures deliberately use Unicode.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
