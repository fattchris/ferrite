#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Plan Build — Ferrite custom: planner (LLM) → builder (LLM) → write files → test.

The planner returns a plan in notes_for_next_agent.
The builder returns file contents in a structured JSON envelope.
This script writes those files, runs quality checks, and commits.
"""

import argparse
import json
import sys
from pathlib import Path

from adw_modules import agents, gates, git_helper, quality, session, utils
from adw_modules.data_types import AgentCall, BuildOutput, PhaseParams, PlanOutput

REQUIRED_AGENTS = ["planner", "builder"]
MAX_FIX_LOOPS = 3


class FileContent:
    """A file to write."""
    path: str
    content: str


def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id)

    # Phase 1: Request
    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the incoming ask")) as ph:
        ph.log(input=prompt)

    # Phase 2: Plan (LLM mode — returns plan in notes_for_next_agent)
    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                               description="Turn the request into an implementable plan")) as ph:
        plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt,
                                 gates=[]))

    # Phase 3: Build (LLM mode — returns file contents in notes_for_next_agent)
    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement the plan, output file contents as JSON")) as ph:
        previous: BuildOutput = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                                   previous=plan, gates=[]))  # type: ignore[assignment]

    # Phase 4: Write files from builder's envelope
    with run.phase(PhaseParams(name="write_files", kind="code", owner="system",
                               description="Write builder's file contents to disk")) as ph:
        files_written = []
        notes = previous.notes_for_next_agent or ""

        # Parse FILE DELIMITER format: <<<FILE: path>>> content <<<END_FILE>>>
        import re
        pattern = re.compile(r'<<<FILE:\s*(.+?)>>>\n(.*?)<<<END_FILE>>>', re.DOTALL)
        for m in pattern.finditer(notes):
            path = m.group(1).strip()
            content = m.group(2)
            if path and content:
                full_path = Path(run.repo_root) / path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(content)
                files_written.append(path)
                ph.log(file=path, lines=content.count("\n") + 1)

        if not files_written:
            # Fallback: try JSON array format (legacy)
            try:
                start = notes.find("[")
                end = notes.rfind("]")
                if start != -1 and end > start:
                    files = json.loads(notes[start:end + 1])
                    for f in files:
                        path = f.get("path", "")
                        content = f.get("content", "")
                        if path and content:
                            full_path = Path(run.repo_root) / path
                            full_path.parent.mkdir(parents=True, exist_ok=True)
                            full_path.write_text(content)
                            files_written.append(path)
                            ph.log(file=path, lines=content.count("\n") + 1)
            except (json.JSONDecodeError, KeyError) as e:
                ph.log(error=f"Failed to parse files from builder: {e}")
                ph.log(raw_notes=notes[:500])

        ph.log(files_written=files_written)

    # Phase 5: Quality checks
    test_result = None
    quality_result = None
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(PhaseParams(name=f"verify_{i}", kind="code", owner="quality",
                                   description="Lint, typecheck, and test")) as ph:
            quality_result = quality.run_quality(run)
            passed = sum(1 for c in quality_result.checks if c.passed)
            ph.log(passed=quality_result.passed, checks=f"{passed}/{len(quality_result.checks)}",
                   artifacts=", ".join(quality_result.artifacts))

        test_result = quality_result

        if quality_result.passed and test_result.passed:
            break
        if i == MAX_FIX_LOOPS:
            break

        # Fix loop — send failures back to builder
        with run.phase(PhaseParams(name=f"fix_{i}", kind="agent", owner="builder", retries=1,
                                   description=f"Resolve verification failures (attempt {i})")) as ph:
            previous = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                         previous=quality.as_envelope(quality_result, "verification"),
                                         gates=[]))  # type: ignore[assignment]

        # Write fixed files
        with run.phase(PhaseParams(name=f"write_fix_{i}", kind="code", owner="system",
                                   description=f"Write fixed files (attempt {i})")) as ph:
            notes = previous.notes_for_next_agent or ""
            # Parse FILE DELIMITER format
            import re
            pattern = re.compile(r'<<<FILE:\s*(.+?)>>>\n(.*?)<<<END_FILE>>>', re.DOTALL)
            for m in pattern.finditer(notes):
                path = m.group(1).strip()
                content = m.group(2)
                if path and content:
                    full_path = Path(run.repo_root) / path
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(content)
                    ph.log(file=path, fixed=True)
            # Fallback: JSON array
            if not any(True for _ in pattern.finditer(notes)):
                try:
                    start = notes.find("[")
                    end = notes.rfind("]")
                    if start != -1 and end > start:
                        files = json.loads(notes[start:end + 1])
                        for f in files:
                            path = f.get("path", "")
                            content = f.get("content", "")
                            if path and content:
                                full_path = Path(run.repo_root) / path
                                full_path.parent.mkdir(parents=True, exist_ok=True)
                                full_path.write_text(content)
                                ph.log(file=path, fixed=True)
                except (json.JSONDecodeError, KeyError) as e:
                    ph.log(error=f"Failed to parse fix files: {e}")

    # Phase 6: Commit
    verified = (quality_result is not None and quality_result.passed
                and test_result is not None and test_result.passed)
    if verified:
        with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                                   description="Commit tested and verified code")) as ph:
            message = previous.commit_message or f"sssf({run.adw_id}): {previous.summary}"
            sha = git_helper.commit_all(message)
            ph.log(sha=sha, message=message)

    return run.finish(accepted=verified,
                      reason=f"verify/test never came back clean after {MAX_FIX_LOOPS} fix attempt(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id))
