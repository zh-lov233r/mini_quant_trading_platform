#!/usr/bin/env python3
"""Start the local Quant + AgentOps development stack safely."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


QUANT_BACKEND_URL = "http://localhost:8000"
QUANT_FRONTEND_URL = "http://localhost:3000"
AGENTOPS_URL = "http://localhost:8100"
AGENTOPS_DATABASE_URL = "postgresql+asyncpg://agentops:agentops@localhost:15432/agentops"
STACK_STATE_FILE = Path(f"/tmp/quant-agent-stack-{os.getuid()}.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start AgentOps PostgreSQL/API and the Quant backend/frontend with trading disabled."
    )
    parser.add_argument(
        "--coding-agent-repo",
        type=Path,
        help="Coding Agent repository. Defaults to CODING_AGENT_REPO or a nearby coding_agent checkout.",
    )
    parser.add_argument(
        "--github-delivery-mode",
        choices=("mock", "gh", "oauth"),
        default=os.getenv("GITHUB_DELIVERY_MODE", "mock"),
        help="Draft PR delivery mode. Defaults to mock.",
    )
    return parser.parse_args()


def find_coding_agent_repo(quant_repo: Path, explicit: Path | None) -> Path:
    configured = explicit or (Path(os.environ["CODING_AGENT_REPO"]) if os.getenv("CODING_AGENT_REPO") else None)
    candidates = [
        configured,
        quant_repo.parent / "coding_agent",
        quant_repo.parent.parent / "coding_agent",
    ]
    for candidate in candidates:
        if candidate and (candidate / "scripts/bootstrap_quant_integration.py").is_file():
            return candidate.resolve()
    searched = ", ".join(str(item) for item in candidates if item)
    raise RuntimeError(
        "Coding Agent repository was not found. Set CODING_AGENT_REPO or pass "
        f"--coding-agent-repo. Checked: {searched}"
    )


def require_file(path: Path, remediation: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"Required file is missing: {path}. {remediation}")


def resolve_executable(configured: str | None) -> str | None:
    if not configured:
        return None
    resolved = shutil.which(configured)
    if resolved:
        return resolved
    candidate = Path(configured).expanduser()
    return str(candidate.resolve()) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def endpoint_ready(url: str, timeout: float = 1.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed localhost URLs
            return 200 <= response.status < 500
    except (OSError, URLError):
        return False


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def process_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def owned_stack_is_ready(state_path: Path, quant_repo: Path) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if state.get("quantRepo") != str(quant_repo):
        return False
    pids = [state.get("controllerPid"), *(state.get("processPids") or [])]
    if len(pids) != 5 or not all(process_alive(pid) for pid in pids):
        return False
    return all(
        (
            endpoint_ready(f"{AGENTOPS_URL}/healthz"),
            endpoint_ready(f"{QUANT_BACKEND_URL}/readyz"),
            endpoint_ready(f"{QUANT_FRONTEND_URL}/research"),
        )
    )


def write_stack_state(
    state_path: Path,
    *,
    quant_repo: Path,
    project_id: str,
    delivery_mode: str,
    processes: list[tuple[str, subprocess.Popen[str]]],
) -> None:
    state_path.write_text(
        json.dumps(
            {
                "controllerPid": os.getpid(),
                "processPids": [process.pid for _, process in processes],
                "quantRepo": str(quant_repo),
                "projectId": project_id,
                "githubDeliveryMode": delivery_mode,
                "paperSchedulerEnabled": False,
                "paperOrderSubmissionEnabled": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    state_path.chmod(0o600)


def remove_owned_stack_state(state_path: Path) -> None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return
    if state.get("controllerPid") == os.getpid():
        state_path.unlink(missing_ok=True)


def reject_partial_stack() -> None:
    occupied = [str(port) for port in (3000, 8000, 8100) if port_in_use(port)]
    if occupied:
        raise RuntimeError(
            "Some required ports are already occupied, but the complete stack is not healthy: "
            f"{', '.join(occupied)}. Stop the stale process(es) and retry."
        )


def run_checked(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout.rstrip(), flush=True)
    if result.returncode:
        raise RuntimeError(f"Command failed with exit code {result.returncode}: {' '.join(command)}")
    return result.stdout


def start_process(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> tuple[str, subprocess.Popen[str]]:
    print(f"Starting {name}...", flush=True)
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        text=True,
        start_new_session=True,
    )
    return name, process


def wait_for_endpoint(name: str, url: str, process: subprocess.Popen[str], timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"{name} exited before becoming ready (exit code {return_code}).")
        if endpoint_ready(url):
            print(f"{name} ready: {url}", flush=True)
            return
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {name}: {url}")


def parse_project_id(output: str) -> str:
    prefix = "NEXT_PUBLIC_AGENTOPS_PROJECT_ID="
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            value = line.removeprefix(prefix).strip()
            if value:
                return value
    raise RuntimeError("AgentOps bootstrap did not return NEXT_PUBLIC_AGENTOPS_PROJECT_ID.")


def stop_processes(processes: list[tuple[str, subprocess.Popen[str]]]) -> None:
    if not processes:
        return
    print("\nStopping application processes started by this command...", flush=True)
    for name, process in reversed(processes):
        if process.poll() is None:
            print(f"Stopping {name}...", flush=True)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 8
    for _, process in reversed(processes):
        remaining = max(0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    print("AgentOps PostgreSQL was left running so local data is preserved.", flush=True)


def main() -> int:
    args = parse_args()
    quant_repo = Path(__file__).resolve().parents[1]
    processes: list[tuple[str, subprocess.Popen[str]]] = []

    try:
        if owned_stack_is_ready(STACK_STATE_FILE, quant_repo):
            print("Quant Agent stack is already running.")
            print(f"Open: {QUANT_FRONTEND_URL}/research")
            return 0
        reject_partial_stack()

        agent_repo = find_coding_agent_repo(quant_repo, args.coding_agent_repo)
        quant_python = quant_repo / ".venv/bin/python"
        agent_python = agent_repo / ".venv/bin/python"
        require_file(quant_python, "Create the Quant virtual environment and install backend requirements first.")
        require_file(agent_python, "Run `make control-plane-install` in the Coding Agent repository first.")
        require_file(
            quant_repo / "frontend/node_modules/.bin/next",
            "Run `npm install` in the Quant frontend directory first.",
        )
        if shutil.which("docker") is None:
            raise RuntimeError("Docker is required to start AgentOps PostgreSQL.")

        codex_bin = resolve_executable(os.getenv("CODEX_BIN") or "codex")
        planner_provider = os.getenv("PLANNER_PROVIDER", "codex_cli")
        if planner_provider == "codex_cli" and not codex_bin:
            raise RuntimeError("Codex CLI is required for the default structured-agent provider.")
        if planner_provider == "codex_cli":
            auth = subprocess.run(
                [codex_bin, "login", "status"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if auth.returncode:
                raise RuntimeError("Codex CLI is not authenticated. Run `codex login` and retry.")

        shared_token = os.getenv("QUANT_AGENT_SERVICE_TOKEN") or secrets.token_urlsafe(32)
        base_env = os.environ.copy()

        print(f"Quant repository: {quant_repo}")
        print(f"Coding Agent repository: {agent_repo}")
        print("Paper scheduler: disabled")
        print("Paper order submission: disabled")
        print(f"GitHub delivery mode: {args.github_delivery_mode}")
        print("Service token: generated in memory / [REDACTED]")

        agent_env = base_env.copy()
        agent_env.update(
            {
                "DATABASE_URL": os.getenv("AGENTOPS_DATABASE_URL", AGENTOPS_DATABASE_URL),
                "QUANT_AGENT_INTEGRATION_ENABLED": "true",
                "QUANT_API_BASE_URL": QUANT_BACKEND_URL,
                "QUANT_AGENT_SERVICE_TOKEN": shared_token,
                "PLANNER_PROVIDER": planner_provider,
                "GITHUB_DELIVERY_MODE": args.github_delivery_mode,
            }
        )
        if codex_bin:
            agent_env["CODEX_BIN"] = codex_bin

        print("Preparing AgentOps PostgreSQL and migrations...", flush=True)
        run_checked(["make", "db-migrate"], cwd=agent_repo, env=agent_env)

        agent_process = start_process(
            "AgentOps API",
            [
                str(agent_python),
                "-m",
                "uvicorn",
                "agentops_control_plane.main:app",
                "--app-dir",
                "apps/control-plane/src",
                "--host",
                "0.0.0.0",
                "--port",
                "8100",
            ],
            cwd=agent_repo,
            env=agent_env,
        )
        processes.append(agent_process)
        wait_for_endpoint("AgentOps API", f"{AGENTOPS_URL}/healthz", agent_process[1], 60)

        print("Publishing/reconciling Quant workflows...", flush=True)
        bootstrap_output = run_checked(
            [
                str(agent_python),
                "scripts/bootstrap_quant_integration.py",
                "--api-base-url",
                AGENTOPS_URL,
                "--quant-repo",
                str(quant_repo),
            ],
            cwd=agent_repo,
            env=agent_env,
        )
        project_id = parse_project_id(bootstrap_output)

        quant_env = base_env.copy()
        if os.getenv("QUANT_DATABASE_URL"):
            quant_env["DATABASE_URL"] = os.environ["QUANT_DATABASE_URL"]
        quant_env.update(
            {
                "FRONTEND_ORIGIN": QUANT_FRONTEND_URL,
                "QUANT_AGENT_INTEGRATION_ENABLED": "true",
                "QUANT_AGENT_SERVICE_TOKEN": shared_token,
                "RESEARCH_WORKER_ENABLED": "true",
                "RESEARCH_WORKER_CONCURRENCY": os.getenv("RESEARCH_WORKER_CONCURRENCY", "2"),
                "PAPER_TRADING_SCHEDULER_ENABLED": "false",
                "PAPER_TRADING_SCHEDULER_SUBMIT_ORDERS": "false",
            }
        )
        quant_backend = start_process(
            "Quant Backend",
            [
                str(quant_python),
                "-m",
                "uvicorn",
                "src.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ],
            cwd=quant_repo / "backend",
            env=quant_env,
        )
        processes.append(quant_backend)
        backtest_manager = start_process(
            "Backtest Worker Manager",
            [str(quant_python), "-m", "src.workers.backtest_worker_manager"],
            cwd=quant_repo / "backend",
            env=quant_env,
        )
        processes.append(backtest_manager)
        wait_for_endpoint("Quant Platform", f"{QUANT_BACKEND_URL}/readyz", quant_backend[1], 60)

        frontend_env = base_env.copy()
        frontend_env.update(
            {
                "NEXT_PUBLIC_API_BASE_URL": QUANT_BACKEND_URL,
                "NEXT_PUBLIC_AGENTOPS_API_BASE_URL": AGENTOPS_URL,
                "NEXT_PUBLIC_AGENTOPS_PROJECT_ID": project_id,
            }
        )
        quant_frontend = start_process(
            "Quant Frontend",
            ["npm", "run", "dev"],
            cwd=quant_repo / "frontend",
            env=frontend_env,
        )
        processes.append(quant_frontend)
        wait_for_endpoint("Quant Frontend", f"{QUANT_FRONTEND_URL}/research", quant_frontend[1], 120)

        write_stack_state(
            STACK_STATE_FILE,
            quant_repo=quant_repo,
            project_id=project_id,
            delivery_mode=args.github_delivery_mode,
            processes=processes,
        )

        print("\nQuant Agent stack is ready.")
        print(f"Research workspace: {QUANT_FRONTEND_URL}/research")
        print(f"Quant API docs: {QUANT_BACKEND_URL}/docs")
        print(f"AgentOps API docs: {AGENTOPS_URL}/docs")
        print("Press Ctrl+C to stop the four application processes.", flush=True)

        stopping = False

        def request_stop(_signum: int, _frame: object) -> None:
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        while not stopping:
            for name, process in processes:
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(f"{name} exited unexpectedly with code {return_code}.")
            time.sleep(0.5)
        return 0
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        remove_owned_stack_state(STACK_STATE_FILE)
        stop_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())
