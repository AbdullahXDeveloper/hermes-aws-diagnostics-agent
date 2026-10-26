import io
import json
import subprocess
from unittest.mock import Mock

import pytest

from aws_diagnostics import bootstrap, cli


@pytest.fixture(autouse=True)
def local_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap.tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setenv("AWS_DIAGNOSTICS_ROLE_ARN", "arn:aws:iam::123456789012:role/Diagnostics")
    monkeypatch.setenv("AWS_DIAGNOSTICS_ACCESS_MODE", "auto")
    monkeypatch.setenv("AWS_DIAGNOSTICS_ALLOW_ROOT", "false")
    check = {"aws_cli": {"status": "ok", "path": "/test/aws", "version": "aws-cli/2.38.5"}}
    monkeypatch.setattr(cli, "doctor", Mock(return_value=check))
    install = Mock(return_value="/test/aws")
    monkeypatch.setattr(cli, "install_aws", install)
    login = Mock(return_value={"status": "connected"})
    monkeypatch.setattr(cli, "login", login)
    return install, login


def test_setup_reuses_cli_and_config_and_backs_up_upgrade(ready, tmp_path, capsys):
    path = tmp_path / "agent.json"
    original = {"theme": "dark", "mcpServers": {"other": {"command": "other"},
                **cli.configuration()["mcpServers"]}}
    path.write_text(json.dumps(original))
    assert cli.main(["setup", "--no-login", "--config", str(path)]) == 0
    capsys.readouterr()
    result = json.loads(path.read_text())
    assert result["theme"] == "dark"
    assert result["mcpServers"]["other"] == {"command": "other"}
    assert result["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_AWS_CLI"] == "/test/aws"
    assert len(list(tmp_path.glob("*.bak-*"))) == 1
    assert cli.main(["setup", "--no-login", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["configuration"]["status"] == "unchanged"
    assert len(list(tmp_path.glob("*.bak-*"))) == 1
    ready[0].assert_not_called()
    ready[1].assert_not_called()


def test_role_setting_reaches_mcp_configuration_and_login(ready, capsys):
    role = "arn:aws:iam::123456789012:role/Diagnostics"
    assert cli.main(["setup", "--role-arn", role]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["configuration"]["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_ROLE_ARN"] == role
    assert ready[1].call_args.kwargs == {"role_arn": role, "access_mode": "auto", "allow_root": False}


def test_setup_installs_and_rechecks_before_configuration(ready, monkeypatch, capsys):
    monkeypatch.setattr(cli, "doctor", Mock(side_effect=[{"aws_cli": {"status": "missing"}},
                        {"aws_cli": {"status": "ok", "path": "/test/aws"}}]))
    monkeypatch.delenv("AWS_DIAGNOSTICS_AWS_CLI", raising=False)
    assert cli.main(["setup", "--no-login"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready"
    ready[0].assert_called_once()


def test_setup_no_install_fails_without_mutation(ready, monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "doctor", Mock(return_value={"aws_cli": {"status": "missing"}}))
    path = tmp_path / "config.json"
    assert cli.main(["setup", "--no-install", "--config", str(path)]) == 2
    assert not path.exists()
    ready[0].assert_not_called()
    ready[1].assert_not_called()


@pytest.mark.parametrize("contents", ["[]", "bad json", '{"mcpServers":{"aws-diagnostics":null}}'])
def test_setup_rejects_conflicting_or_bad_config_before_install(ready, tmp_path, contents):
    path = tmp_path / "config.json"
    path.write_text(contents)
    assert cli.main(["setup", "--config", str(path)]) == 2
    assert path.read_text() == contents
    ready[0].assert_not_called()
    ready[1].assert_not_called()


def test_setup_forwards_login_settings(ready, capsys):
    assert cli.main(["setup", "--profile", "company", "--region", "eu-west-1",
                     "--auth-mode", "sso", "--remote"]) == 0
    ready[1].assert_called_once_with("/test/aws", "company", "eu-west-1", "sso", True,
                                   role_arn="arn:aws:iam::123456789012:role/Diagnostics",
                                   access_mode="auto", allow_root=False)
    assert json.loads(capsys.readouterr().out)["login"]["status"] == "connected"


def test_setup_retains_config_if_login_fails(ready, tmp_path, capsys):
    ready[1].side_effect = subprocess.TimeoutExpired("aws", 600)
    path = tmp_path / "config.json"
    assert cli.main(["setup", "--config", str(path)]) == 1
    assert path.is_file()
    assert json.loads(capsys.readouterr().out)["status"] == "login_required"


@pytest.mark.parametrize("extra", [[], ["--no-login"]])
def test_missing_role_is_not_reported_ready_or_sent_to_login(ready, monkeypatch, tmp_path, capsys, extra):
    monkeypatch.delenv("AWS_DIAGNOSTICS_ROLE_ARN", raising=False)
    path = tmp_path / "agent.json"
    assert cli.main(["setup", "--access-mode", "role", "--config", str(path), *extra]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "security_setup_required"
    assert result["security"]["reason"] == "missing_role_arn"
    assert result["login"]["status"] == "not_started"
    assert path.is_file()
    assert "--role-arn" in result["configuration"]["next_step"]
    ready[1].assert_not_called()


@pytest.mark.parametrize("mode", ["auto", "direct"])
def test_setup_without_role_can_login_using_source_profile(ready, monkeypatch, tmp_path, capsys, mode):
    monkeypatch.delenv("AWS_DIAGNOSTICS_ROLE_ARN", raising=False)
    path = tmp_path / "agent.json"
    assert cli.main(["setup", "--access-mode", mode, "--config", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ready"
    assert result["access"] == {"mode": "direct", "read_only_enforced": False, "root_opt_in": False}
    assert "AWS_DIAGNOSTICS_ROLE_ARN" not in json.loads(path.read_text())["mcpServers"]["aws-diagnostics"]["env"]
    assert ready[1].call_args.kwargs == {"access_mode": mode, "allow_root": False}


def test_root_opt_in_is_forwarded_to_login_and_saved_config(ready, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("AWS_DIAGNOSTICS_ROLE_ARN", raising=False)
    path = tmp_path / "agent.json"
    assert cli.main(["setup", "--access-mode", "direct", "--allow-root", "--config", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["access"]["root_opt_in"] is True
    assert ready[1].call_args.kwargs == {"access_mode": "direct", "allow_root": True}
    assert json.loads(path.read_text())["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_ALLOW_ROOT"] == "true"


def test_setup_preserves_existing_role_when_flag_is_omitted(ready, monkeypatch, tmp_path, capsys):
    role = "arn:aws:iam::123456789012:role/ExistingDiagnostics"
    path = tmp_path / "agent.json"
    path.write_text(json.dumps(cli.configuration(role_arn=role)))
    monkeypatch.delenv("AWS_DIAGNOSTICS_ROLE_ARN", raising=False)
    assert cli.main(["setup", "--no-login", "--config", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert json.loads(path.read_text())["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_ROLE_ARN"] == role


def test_invalid_role_rejected_before_install_login_or_config_write(ready, tmp_path):
    path = tmp_path / "agent.json"
    assert cli.main(["setup", "--role-arn", "arn:aws:sts::123456789012:assumed-role/Diagnostics/session",
                     "--config", str(path)]) == 2
    assert not path.exists()
    ready[0].assert_not_called()
    ready[1].assert_not_called()


def test_setup_reports_security_error_without_requesting_another_login(ready, capsys):
    ready[1].return_value = {"status": "security_setup_required", "reason": "root_identity"}
    assert cli.main(["setup"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "security_setup_required"


@pytest.mark.parametrize("system,suffix", [("win32", "install.ps1"), ("linux", "install.sh"),
                                          ("darwin", "install.sh")])
def test_installer_uses_official_https_script_and_bounded_process(monkeypatch, system, suffix):
    monkeypatch.setattr(bootstrap.sys, "platform", system)
    monkeypatch.setenv("PSModulePath", "inherited-powershell-7-modules")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/shell")
    download = Mock(return_value=io.BytesIO(b"# official installer test double"))
    monkeypatch.setattr(bootstrap, "urlopen", download)
    launch = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(bootstrap.subprocess, "run", launch)
    monkeypatch.setattr(bootstrap, "find_aws", lambda **_: "/installed/aws")
    assert bootstrap.install_aws() == "/installed/aws"
    download.assert_called_once_with(f"https://awscli.amazonaws.com/v2/{suffix}", timeout=60)
    assert launch.call_args.args[0][-1].endswith(suffix)
    assert launch.call_args.kwargs["timeout"] == 600
    assert "shell" not in launch.call_args.kwargs
    module_keys = [key for key in launch.call_args.kwargs["env"] if key.upper() == "PSMODULEPATH"]
    if system == "win32":
        assert not module_keys
    else:
        assert module_keys
        assert launch.call_args.kwargs["env"][module_keys[0]] == "inherited-powershell-7-modules"


def test_installer_failure_does_not_report_success(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: "/bin/bash")
    monkeypatch.setattr(bootstrap, "urlopen", Mock(return_value=io.BytesIO(b"test")))
    monkeypatch.setattr(bootstrap.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    with pytest.raises(ValueError, match="installer failed"):
        bootstrap.install_aws()


def test_login_uses_foreground_cli_and_verifies_identity(monkeypatch):
    connection = Mock()
    connection.identity.side_effect = [{"status": "reconnect_required"}, {"status": "connected"}]
    connection.mode.return_value = "console"
    monkeypatch.setattr("aws_diagnostics.auth.Connection", Mock(return_value=connection))
    launch = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(bootstrap.subprocess, "run", launch)
    assert bootstrap.login("/aws", "personal", "us-east-1", "console", True) == {"status": "connected"}
    assert launch.call_args.args[0] == ["/aws", "login", "--remote", "--profile", "personal",
                                        "--region", "us-east-1", "--no-cli-pager"]
    connection.close.assert_called_once()


def test_find_aws_uses_explicit_configured_path(monkeypatch):
    monkeypatch.setenv("AWS_DIAGNOSTICS_AWS_CLI", "/chosen/aws")
    assert bootstrap.find_aws() == "/chosen/aws"
