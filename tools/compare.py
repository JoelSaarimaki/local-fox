"""
Runs the comparison scenarios of ollmcp-generic-tools (local_ai_tests/compare_scenarios.py) through the core of
Local Fox, so that the measurements show how a model behaves in Local Fox, with the same checks and summary as
compare_models.py.

    python tools/compare.py [--tools-repo ../ollmcp-generic-tools] [--models flint:40b,gemma4:26b]
                            [--scenarios plan_new,implement_fix] [--seeds 1,2,3] [--context 32000] [--no-think]

Run it with the Python that has ollmcp-generic-tools and ollmcp installed, as the scenarios' harness needs both.
Differences from compare_models.py, as they are differences of Local Fox from ollmcp:
- The session starts as Local Fox starts it: the instructions and the filemap in the system message, not a
  "Read the project instructions." exchange. The instructions are Local Fox's template, as its setup writes
  them into a project (--instructions tools uses the tools' own template instead).
- The tools have their own names, a call by a prefixed name runs too, and a faulty call gets an error response.
- Nothing is sent on the model's behalf: a reply that stops after announcing a step ends the run, and is
  counted as a stall, but no "Continue." is sent.
Every tool call is allowed, as the user would allow all for the session. Transcripts and summary.md go to --out.
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from local_fox import config as config_module  # noqa: E402
from local_fox import conversation as c  # noqa: E402
from local_fox import session_log  # noqa: E402
from local_fox.session import Session  # noqa: E402

async def run(model: str, scenario: dict, seed: int, args, out: Path, cm, harness) -> dict:
    work = Path(args.work)
    home = work / "home"
    project = work / f"project-{seed}"
    files = {**cm.PROJECT, **scenario.get("files", {})}
    make_project = harness.make_project
    instructions = None
    if args.instructions != "tools":
        # The harness builds the project with the tools' .ollmcp-tools folder, so the template's paths are moved there
        instructions = work / "instructions.md"
        instructions.parent.mkdir(parents=True, exist_ok=True)
        text = Path(args.instructions).read_text(encoding="utf-8").replace(".local-fox/", ".ollmcp-tools/")
        instructions.write_text(text, encoding="utf-8")
    make_project(project, home, files, instructions)
    cm._git(project, "init", "-q")
    cm._git(project, "add", "-A")
    cm._git(project, "commit", "-q", "-m", "Start")
    before = dict(files)

    settings = {**config_module.DEFAULTS, "model": model, "num_ctx": args.context, "seed": seed, "max_steps": None,
                "think": False if args.no_think else None, "temperature": args.temperature}
    session = Session(settings, project, extra_env={"PYTHONPATH": str(harness.REPO), "USERPROFILE": str(home), "HOME": str(home)},
                      log_dir=work / "logs")
    session.allow_all = True
    started = time.monotonic()
    await session.start()
    session.allow_all = True
    asks_mode = "`Mode: " in (project / ".ollmcp-tools" / "instructions.md").read_text(encoding="utf-8")
    session.add_history([m if asks_mode or m["role"] != "assistant" else {**m, "content": cm.MODE_LINE.sub("", m["content"])}
                         for m in scenario.get("history", [])])
    history_entries = len(session.entries)
    try:
        await asyncio.wait_for(session.send(scenario["request"]), timeout=args.timeout)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        await session.close()

    entries = session.entries[history_entries:]
    calls, texts, transcript, unknown = [], [], [], 0
    for entry in entries:
        if entry.kind == c.ASSISTANT:
            if entry.text.strip():
                texts.append(entry.text)
            transcript.append({"assistant": entry.text, "thinking": entry.thinking[:4000] or None})
        elif entry.kind == c.TOOL:
            transcript.append({"tool": entry.tool, "ran": entry.resolved or None, "args": entry.args, "response": entry.response[:3000]})
            if not entry.resolved:
                unknown += 1
                continue
            call_args = entry.args if isinstance(entry.args, dict) else {}
            calls.append({"tool": entry.resolved, "args": call_args, "paths": cm._paths(call_args),
                          "error": entry.status == c.FAILED})
        elif entry.kind in (c.NOTICE, c.ERROR):
            transcript.append({entry.kind: entry.text})
    reply = next((e.text for e in reversed(entries) if e.kind == c.ASSISTANT and e.text.strip()), "")
    finished = not timed_out and bool(reply) and not any(e.kind == c.ERROR for e in entries)
    checks = cm._check(scenario, project, calls, texts, reply, before)
    checks |= {"unknown_name_calls": unknown, "stalls": int(finished and cm._stalled(reply, calls)), "finished": finished,
               "seconds": round(time.monotonic() - started), "output_tokens": 0}
    record = {"model": model, "scenario": scenario["name"], "seed": seed, "harness": "local-fox", "checks": checks, "reply": reply,
              "diff": cm._git(project, "diff"),
              "plans": {p: (project / p).read_text(encoding="utf-8", errors="replace") for p in checks["plan_files_written"]},
              "transcript": transcript}
    path = out / f"{model.replace(':', '_')}--{scenario['name']}--{seed}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return record

async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tools-repo", default=str(ROOT.parent / "ollmcp-generic-tools"))
    parser.add_argument("--models", default="flint:40b")
    parser.add_argument("--scenarios", default=None)
    parser.add_argument("--seeds", default="1,2,3")
    parser.add_argument("--context", type=int, default=32000)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--no-think", action="store_true")
    parser.add_argument("--instructions", default=str(ROOT / "local_fox" / "templates" / "instructions.md"),
                        help="The instructions.md to test: Local Fox's template by default, 'tools' for the tools' own template, or a file.")
    parser.add_argument("--timeout", type=int, default=3600, help="Seconds before a run is stopped.")
    parser.add_argument("--work", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    tests = Path(args.tools_repo).resolve() / "local_ai_tests"
    sys.path.insert(0, str(tests))
    import compare_models as cm  # noqa: E402  From the tools repository
    import harness  # noqa: E402

    out = Path(args.out) if args.out else ROOT / "runs" / "compare" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    args.work = args.work or str(ROOT / "runs" / "projects")
    session_log.reset(Path(args.work) / "logs")  # The log of this comparison run only
    names = args.scenarios.split(",") if args.scenarios else [s["name"] for s in cm.SCENARIOS]
    scenarios = [s for s in cm.SCENARIOS if s["name"] in names]
    seeds = [int(s) for s in args.seeds.split(",")]
    records = []
    for model in args.models.split(","):
        for scenario in scenarios:
            for seed in seeds:
                path = out / f"{model.replace(':', '_')}--{scenario['name']}--{seed}.json"
                if path.exists():
                    records.append(json.loads(path.read_text(encoding="utf-8")))
                    continue
                try:
                    record = await run(model, scenario, seed, args, out, cm, harness)
                except Exception as error:
                    print(f"{model} {scenario['name']} seed {seed}: the run failed: {error!r}", flush=True)
                    continue
                records.append(record)
                k = record["checks"]
                print(f"{model} {scenario['name']} seed {seed}: acted {k['acted_mode']} ({'ok' if k['acted_mode_ok'] else 'WRONG'}), "
                      f"read {k['required_read']}, calls {k['calls']}, errors {k['errors']}, unknown names {k['unknown_name_calls']}, "
                      f"grader {k.get('grader_pass', '-')}, finished {k['finished']}, {k['seconds']} s", flush=True)
                (out / "summary.md").write_text(cm.summarize(records), encoding="utf-8")
    print(cm.summarize(records))
    print(f"Transcripts and summary in {out}")

if __name__ == "__main__":
    asyncio.run(main())
