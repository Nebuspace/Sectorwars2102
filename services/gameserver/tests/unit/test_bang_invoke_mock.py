"""Phase 4B subprocess-mock coverage for ``BangImportService.invoke_bang``.

Supplements ``test_bang_import_service.TestInvokeBangSubprocess`` with the
edge cases called out in the integration plan's Failure Mode Matrix:

* Timeout → RuntimeError
* Schema version mismatch → ValueError
* Non-zero exit captures stderr in the exception message
* JSON parse error captures (truncated) stdout in the exception
* Progress-stderr forwarding to the log sink

All tests inject a synchronous fake via ``subprocess_runner``; nothing
real is spawned.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any, Dict, List

import pytest

from src.schemas.bang_config import BangConfig
from src.services.bang_import_service import BangImportService


def _minimal_universe(region_type: str, total: int = 300) -> Dict[str, Any]:
    """A bare-bones valid Universe blob the shape-validator accepts."""
    return {
        "version": "1.3.0",
        "seed": 42,
        "totalSectors": total,
        "sectors": {},
        "warps": [],
    }


@pytest.fixture
def config_terran() -> BangConfig:
    return BangConfig(seed=42, sectors=300, region_type="terran_space")


@pytest.fixture
def config_player_owned() -> BangConfig:
    return BangConfig(seed=42, sectors=1000, region_type="player_owned")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeBangHappy:
    """The happy path: zero exit, valid JSON, valid Universe shape."""

    def test_returns_parsed_universe(self, config_terran: BangConfig) -> None:
        universe = _minimal_universe("terran_space", total=300)

        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(universe), stderr=""
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        result = svc.invoke_bang(config_terran)
        assert result.region_type == "terran_space"
        assert result.total_sectors == 300
        assert result.version == "1.3.0"
        assert result.seed == 42


# ---------------------------------------------------------------------------
# Non-zero exit
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeBangNonZeroExit:
    """The subprocess returned non-zero — captured stderr surfaces in the error."""

    def test_captures_stderr_tail(self, config_terran: BangConfig) -> None:
        stderr_blob = "boom: cluster rescue ran out of attempts at sector 142"

        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr=stderr_blob
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(RuntimeError) as excinfo:
            svc.invoke_bang(config_terran)
        assert "exited 1" in str(excinfo.value)
        assert "cluster rescue ran out" in str(excinfo.value)
        assert "terran_space" in str(excinfo.value)

    def test_exit_code_two_in_invoke_also_raises(
        self, config_terran: BangConfig
    ) -> None:
        # `--json-out` only treats 0 as success; validate_only also accepts 2.
        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=2, stdout="", stderr="warnings"
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(RuntimeError, match="exited 2"):
            svc.invoke_bang(config_terran)


# ---------------------------------------------------------------------------
# Bad JSON
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeBangBadJson:
    """Stdout was not valid JSON — exception names the region and quotes parser."""

    def test_garbage_stdout(self, config_player_owned: BangConfig) -> None:
        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="<<NOT JSON>>", stderr=""
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(RuntimeError) as excinfo:
            svc.invoke_bang(config_player_owned)
        msg = str(excinfo.value)
        assert "invalid JSON" in msg
        assert "player_owned" in msg

    def test_truncated_json(self, config_player_owned: BangConfig) -> None:
        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout='{"version":"1.3', stderr=""
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(RuntimeError, match="invalid JSON"):
            svc.invoke_bang(config_player_owned)


# ---------------------------------------------------------------------------
# Schema / shape mismatches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeBangSchemaMismatch:
    """Stdout was JSON but the Universe shape is wrong."""

    def test_missing_required_keys(self, config_terran: BangConfig) -> None:
        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps({"version": "1.3.0", "seed": 1}),
                stderr="",
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(ValueError, match="missing required keys"):
            svc.invoke_bang(config_terran)

    def test_unsupported_major_version(self, config_terran: BangConfig) -> None:
        bad = _minimal_universe("terran_space", total=300)
        bad["version"] = "2.0.0"

        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(bad), stderr=""
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(ValueError, match="not in supported 1.x"):
            svc.invoke_bang(config_terran)

    def test_wrong_sector_count_for_region(
        self, config_terran: BangConfig
    ) -> None:
        bad = _minimal_universe("terran_space", total=42)  # terran_space wants 300

        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(bad), stderr=""
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(ValueError, match="expected 300 sectors"):
            svc.invoke_bang(config_terran)


# ---------------------------------------------------------------------------
# Subprocess timeout
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeBangTimeout:
    """``subprocess.TimeoutExpired`` is wrapped into RuntimeError with region tag."""

    def test_timeout_raises_runtimeerror(self, config_terran: BangConfig) -> None:
        def fake_run(*_a: Any, **kw: Any) -> subprocess.CompletedProcess:
            timeout_value = kw.get("timeout", 0)
            raise subprocess.TimeoutExpired(cmd="bang", timeout=timeout_value)

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        with pytest.raises(RuntimeError) as excinfo:
            svc.invoke_bang(config_terran, timeout_seconds=7)
        msg = str(excinfo.value)
        assert "timed out after 7s" in msg
        assert "terran_space" in msg


# ---------------------------------------------------------------------------
# Stderr forwarding to log sink
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvokeBangStderrForwarding:
    """Bang's stderr lines are pumped into the configured log_sink."""

    def test_no_sink_is_a_no_op(self, config_terran: BangConfig) -> None:
        universe = _minimal_universe("terran_space", total=300)

        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(universe),
                stderr='{"ts":"...","level":"warn","code":"B-040"}\n',
            )

        # No log_sink configured — should not blow up.
        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        result = svc.invoke_bang(config_terran)
        assert result.total_sectors == 300

    def test_sink_receives_lines_inside_event_loop(
        self, config_terran: BangConfig
    ) -> None:
        universe = _minimal_universe("terran_space", total=300)
        received: List[str] = []

        async def sink(line: str) -> None:
            received.append(line)

        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(universe),
                stderr="progress line 1\nprogress line 2\n",
            )

        svc = BangImportService(
            bang_image="t",
            subprocess_runner=fake_run,
            log_sink=sink,
        )

        async def runner() -> None:
            # Allow the create_task'd sink coroutines to complete.
            svc.invoke_bang(config_terran)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        asyncio.run(runner())
        # Two progress lines should arrive (newlines preserved).
        assert len(received) == 2
        assert received[0].startswith("progress line 1")
        assert received[1].startswith("progress line 2")


# ---------------------------------------------------------------------------
# validate_only — additional edge coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateOnlyExtra:
    """Extra cases for the preview path."""

    def test_unexpected_exit_code_raises(self) -> None:
        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=99, stdout="", stderr="catastrophe"
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        config = BangConfig(seed=1, sectors=1000, region_type="player_owned")
        with pytest.raises(RuntimeError, match="exited 99"):
            svc.validate_only(config)

    def test_empty_stdout_yields_empty_report(self) -> None:
        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=""
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        config = BangConfig(seed=1, sectors=1000, region_type="player_owned")
        report = svc.validate_only(config)
        assert report.stats == {}
        assert report.warnings == []
        assert report.validation == {}

    def test_malformed_stdout_does_not_raise(self) -> None:
        # JSONDecodeError is swallowed inside validate_only (intentional).
        def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout="<<not json>>", stderr=""
            )

        svc = BangImportService(bang_image="t", subprocess_runner=fake_run)
        config = BangConfig(seed=1, sectors=1000, region_type="player_owned")
        report = svc.validate_only(config)
        assert report.stats == {}


# ---------------------------------------------------------------------------
# _build_docker_args — additional optional flag coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDockerArgExtras:
    """Optional knobs flow through to the kebab-case CLI flags."""

    def test_stardock_enabled_is_a_bool_toggle(self) -> None:
        svc = BangImportService(bang_image="t")
        config = BangConfig(
            seed=1,
            sectors=300,
            region_type="terran_space",
            stardock_enabled=True,
        )
        args = svc._build_docker_args(config)  # pylint: disable=protected-access
        assert "--stardock-enabled" in args
        # Bool flag has no following value
        idx = args.index("--stardock-enabled")
        assert args[idx + 1] != "True"

    def test_stardock_disabled_omits_flag(self) -> None:
        svc = BangImportService(bang_image="t")
        config = BangConfig(
            seed=1,
            sectors=300,
            region_type="terran_space",
            stardock_enabled=False,
        )
        args = svc._build_docker_args(config)  # pylint: disable=protected-access
        assert "--stardock-enabled" not in args

    def test_port_planet_nebula_percent_emitted(self) -> None:
        svc = BangImportService(bang_image="t")
        config = BangConfig(
            seed=1,
            sectors=1000,
            region_type="player_owned",
            port_percent=18.0,
            planet_percent=22.0,
            nebula_percent=4.0,
        )
        args = svc._build_docker_args(config)  # pylint: disable=protected-access
        for flag in ("--port-percent", "--planet-percent", "--nebula-percent"):
            assert flag in args

    def test_validator_strictness_is_intentionally_absent(self) -> None:
        # Per the schema comment: bang has no strictness flag today.
        svc = BangImportService(bang_image="t")
        config = BangConfig(
            seed=1,
            sectors=1000,
            region_type="player_owned",
            validator_strictness="strict",
        )
        args = svc._build_docker_args(config)  # pylint: disable=protected-access
        assert "--validator-strictness" not in args
        # And the flag map doesn't include the snake_case form either.
        assert "validator_strictness" not in args
