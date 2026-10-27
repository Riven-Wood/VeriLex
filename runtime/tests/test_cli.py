import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from verilex_runtime import cli as cli_module


QA_PLAIN_DOCX = (
    Path(__file__).parents[2] / "qa" / "documents" / "fixtures" / "v1" / "plain.docx"
)


def test_cli_docx_smoke_runs_before_token_validation_without_leaking_text():
    env = os.environ.copy()
    env.pop("VERILEX_API_TOKEN", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--docx-extract-smoke",
            str(QA_PLAIN_DOCX),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "paragraph_count": 1,
        "table_count": 0,
        "warning_codes": [],
    }
    assert "虚构合同正文" not in result.stdout
    assert str(QA_PLAIN_DOCX) not in result.stdout


def test_cli_docx_smoke_rejects_invalid_input_without_path_or_body(tmp_path):
    path = tmp_path / "private-customer-name.docx"
    path.write_text("private-customer-body", encoding="utf-8")
    env = os.environ.copy()
    env.pop("VERILEX_API_TOKEN", None)

    result = subprocess.run(
        [sys.executable, "-m", "verilex_runtime", "--docx-extract-smoke", str(path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "private-customer-name" not in result.stderr
    assert "private-customer-body" not in result.stderr


def test_cli_docx_v1_smoke_creates_and_reextracts_without_leaking_text():
    env = os.environ.copy()
    env.pop("VERILEX_API_TOKEN", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--docx-v1-smoke",
            str(QA_PLAIN_DOCX),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "created": True,
        "extracted": True,
        "ok": True,
    }
    assert "虚构合同正文" not in result.stdout
    assert str(QA_PLAIN_DOCX) not in result.stdout


def test_cli_refuses_to_start_without_launch_token(tmp_path):
    env = os.environ.copy()
    env.pop("VERILEX_API_TOKEN", None)
    result = subprocess.run(
        [sys.executable, "-m", "verilex_runtime", "--state-dir", str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "VERILEX_API_TOKEN is required" in result.stderr


def test_cli_reports_an_ephemeral_port_only_after_binding_it(tmp_path):
    nonce = "a" * 32
    ready_path = tmp_path / f"runtime-{nonce}.ready"
    env = os.environ.copy()
    env["VERILEX_API_TOKEN"] = "ready-test-token"
    env.pop("VERILEX_EXIT_WITH_PARENT", None)
    env.pop("VERILEX_PARENT_PID", None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--port",
            "0",
            "--state-dir",
            str(tmp_path),
            "--ready-nonce",
            nonce,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while (
            not ready_path.exists()
            and process.poll() is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        assert ready_path.exists(), process.stderr.read()
        ready_payload = ready_path.read_text(encoding="utf-8")
        port_text, proof = ready_payload.splitlines()
        port = int(port_text)
        assert 0 < port <= 65535
        expected_proof = hmac.new(
            b"ready-test-token",
            f"{nonce}\0{port}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert proof == expected_proof
        assert "ready-test-token" not in ready_payload
        with socket.socket() as competing_listener:
            with pytest.raises(OSError):
                competing_listener.bind(("127.0.0.1", port))
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        if process.stderr is not None:
            process.stderr.close()

    assert not ready_path.exists()


def test_cli_exits_when_its_desktop_parent_is_gone(tmp_path):
    env = os.environ.copy()
    env["VERILEX_API_TOKEN"] = "parent-watch-test-token"
    env["VERILEX_EXIT_WITH_PARENT"] = "1"
    env["VERILEX_PARENT_PID"] = str(2**31 - 1)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "verilex_runtime",
            "--port",
            "0",
            "--state-dir",
            str(tmp_path),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = None
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)

    assert returncode == 0, "Runtime survived after its desktop parent disappeared"


def test_cli_dispatches_mcp_watchdog_before_api_token_validation(monkeypatch):
    captured = {}

    def run_watchdog(arguments):
        captured["arguments"] = arguments
        return 17

    monkeypatch.delenv("VERILEX_API_TOKEN", raising=False)
    monkeypatch.setattr(cli_module, "run_mcp_watchdog", run_watchdog)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verilex-runtime",
            "--mcp-watchdog",
            "--parent-pid",
            "123",
            "--",
            "fixture-command",
        ],
    )

    def exit_with(code):
        raise SystemExit(code)

    monkeypatch.setattr(cli_module.sys, "exit", exit_with)

    with pytest.raises(SystemExit) as raised:
        cli_module.main()

    assert raised.value.code == 17
    assert captured["arguments"] == [
        "--parent-pid",
        "123",
        "--",
        "fixture-command",
    ]
