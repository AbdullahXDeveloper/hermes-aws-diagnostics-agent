import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from aws_diagnostics.cli import configuration, doctor, main, write_configuration


def test_config_uses_installed_runtime_and_remote_settings():
    result = configuration(profile="personal", region="eu-west-1", auth_mode="console", remote=True)
    entry = result["mcpServers"]["aws-diagnostics"]
    assert Path(entry["command"]).is_absolute()
    assert entry["args"] == ["-m", "aws_diagnostics", "serve"]
    assert entry["env"]["AWS_DIAGNOSTICS_REMOTE"] == "true"
    assert entry["env"]["AWS_DIAGNOSTICS_PROFILE"] == "personal"


def test_merge_preserves_config_and_creates_backup(tmp_path):
    path = tmp_path / "agent.json"
    original = '{"theme":"dark","mcpServers":{"another":{"command":"other"}}}'
    path.write_text(original)
    result = write_configuration(path, configuration())
    updated = json.loads(path.read_text())
    assert updated["theme"] == "dark"
    assert updated["mcpServers"]["another"] == {"command": "other"}
    assert Path(result["backup"]).read_text() == original
    assert write_configuration(path, configuration())["status"] == "unchanged"
    assert len(list(tmp_path.glob("*.bak-*"))) == 1


def test_conflict_requires_explicit_replacement(tmp_path):
    path = tmp_path / "agent.json"
    write_configuration(path, configuration(profile="first"))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="--replace"):
        write_configuration(path, configuration(profile="second"))
    assert path.read_bytes() == original
    write_configuration(path, configuration(profile="second"), replace=True)
    assert json.loads(path.read_text())["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_PROFILE"] == "second"


@pytest.mark.parametrize("contents", ["not json", "[]", '{"mcpServers":[]}'])
def test_invalid_existing_config_is_untouched(tmp_path, contents):
    path = tmp_path / "agent.json"
    path.write_text(contents)
    with pytest.raises(ValueError):
        write_configuration(path, configuration())
    assert path.read_text() == contents


def test_config_print_only(capsys):
    assert main(["configure", "--remote"]) == 0
    assert json.loads(capsys.readouterr().out)["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_REMOTE"] == "true"


def test_configure_preserves_role_on_replacement_without_role_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("AWS_DIAGNOSTICS_ROLE_ARN", raising=False)
    role = "arn:aws:iam::123456789012:role/ExistingDiagnostics"
    path = tmp_path / "agent.json"
    write_configuration(path, configuration(role_arn=role))
    assert main(["configure", "--config", str(path), "--region", "ap-south-1", "--replace"]) == 0
    env = json.loads(path.read_text())["mcpServers"]["aws-diagnostics"]["env"]
    assert env["AWS_DIAGNOSTICS_ROLE_ARN"] == role
    assert env["AWS_REGION"] == "ap-south-1"


def test_configuration_uses_explicit_role_before_environment(monkeypatch):
    monkeypatch.setenv("AWS_DIAGNOSTICS_ROLE_ARN", "arn:aws:iam::123456789012:role/Environment")
    explicit = "arn:aws:iam::123456789012:role/Explicit"
    assert configuration()["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_ROLE_ARN"].endswith("/Environment")
    assert configuration(role_arn=explicit)["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_ROLE_ARN"] == explicit


def test_doctor_reports_missing_aws_without_logging_in(monkeypatch):
    monkeypatch.setattr("aws_diagnostics.cli.find_aws", lambda: None)
    launch = Mock()
    monkeypatch.setattr("aws_diagnostics.cli.subprocess.run", launch)
    assert doctor()["aws_cli"]["status"] == "missing"
    launch.assert_not_called()


def test_doctor_checks_only_cli_version(monkeypatch):
    monkeypatch.setattr("aws_diagnostics.cli.shutil.which", lambda _: "aws")
    launch = Mock(return_value=Mock(returncode=0, stdout="aws-cli/2.38.5", stderr=""))
    monkeypatch.setattr("aws_diagnostics.cli.subprocess.run", launch)
    assert doctor()["aws_cli"]["status"] == "ok"
    assert launch.call_args.args[0] == ["aws", "--version"]
