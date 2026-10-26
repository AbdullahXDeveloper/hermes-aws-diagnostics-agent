from unittest.mock import Mock

import pytest

from aws_diagnostics.auth import Connection
from aws_diagnostics.cli import configuration, main
from aws_diagnostics.diagnostics import Diagnostics


ROLE = "arn:aws:iam::123456789012:role/Diagnostics"


@pytest.fixture(autouse=True)
def clean_access_settings(monkeypatch):
    for name in ("AWS_DIAGNOSTICS_ROLE_ARN", "AWS_DIAGNOSTICS_ACCESS_MODE", "AWS_DIAGNOSTICS_ALLOW_ROOT"):
        monkeypatch.delenv(name, raising=False)


def source(monkeypatch, connection, arn):
    base = Mock()
    base.client.return_value.get_caller_identity.return_value = {"Arn": arn, "Account": "123456789012"}
    monkeypatch.setattr(connection, "base_session", lambda _: base)
    return base


@pytest.mark.parametrize("arn", ["arn:aws:iam::123456789012:user/test",
    "arn:aws:sts::123456789012:assumed-role/CompanySSO/test"])
def test_auto_without_role_uses_existing_non_root_source(monkeypatch, arn):
    connection = Connection()
    base = source(monkeypatch, connection, arn)
    assert connection.session("ap-south-1") is base
    base.client.return_value.assume_role.assert_not_called()
    result = connection.identity()
    assert result["status"] == "connected"
    assert result["access_mode"] == "direct"
    assert result["read_only_enforced"] is False
    assert result["read_only_operations_only"] is True


def test_auto_with_role_keeps_restricted_role_mode():
    connection = Connection(role_arn=ROLE)
    assert connection.uses_role is True
    assert connection.access_details()["read_only_enforced"] is True


def test_explicit_direct_mode_does_not_assume_configured_role(monkeypatch):
    connection = Connection(role_arn=ROLE, access_mode="direct")
    base = source(monkeypatch, connection, "arn:aws:iam::123456789012:user/test")
    assert connection.session() is base
    base.client.return_value.assume_role.assert_not_called()


def test_root_remains_blocked_by_default(monkeypatch):
    connection = Connection()
    source(monkeypatch, connection, "arn:aws:iam::123456789012:root")
    result = connection.identity()
    assert result["status"] == "security_setup_required"
    assert result["reason"] == "root_identity"


def test_explicit_root_opt_in_allows_direct_reads_without_claiming_restriction(monkeypatch):
    connection = Connection(access_mode="direct", allow_root=True)
    base = source(monkeypatch, connection, "arn:aws:iam::123456789012:root")
    result = connection.identity()
    assert result["status"] == "connected"
    assert result["root_identity"] is True
    assert result["read_only_enforced"] is False
    base.client.return_value.assume_role.assert_not_called()


def test_root_opt_in_does_not_enable_root_assume_role(monkeypatch):
    connection = Connection(role_arn=ROLE, allow_root=True)
    base = source(monkeypatch, connection, "arn:aws:iam::123456789012:root")
    assert connection.identity()["reason"] == "root_identity"
    base.client.return_value.assume_role.assert_not_called()


@pytest.mark.parametrize("value", ["maybe", "enabled", "2"])
def test_invalid_root_opt_in_does_not_silently_enable_root(monkeypatch, value):
    monkeypatch.setenv("AWS_DIAGNOSTICS_ALLOW_ROOT", value)
    with pytest.raises(ValueError, match="ALLOW_ROOT"):
        Connection()
    with pytest.raises(ValueError, match="ALLOW_ROOT"):
        configuration()


def test_configure_preserves_access_mode_and_can_revoke_root_opt_in(tmp_path, capsys):
    import json
    path = tmp_path / "agent.json"
    assert main(["configure", "--config", str(path), "--access-mode", "direct", "--allow-root"]) == 0
    capsys.readouterr()
    assert main(["configure", "--config", str(path), "--region", "ap-south-1", "--replace"]) == 0
    env = json.loads(path.read_text())["mcpServers"]["aws-diagnostics"]["env"]
    assert env["AWS_DIAGNOSTICS_ACCESS_MODE"] == "direct"
    assert env["AWS_DIAGNOSTICS_ALLOW_ROOT"] == "true"
    assert main(["configure", "--config", str(path), "--no-allow-root", "--replace"]) == 0
    assert json.loads(path.read_text())["mcpServers"]["aws-diagnostics"]["env"]["AWS_DIAGNOSTICS_ALLOW_ROOT"] == "false"


@pytest.mark.parametrize("principal,allow_root", [("user/test", False), ("root", True)])
def test_direct_discovery_needs_no_diagnostics_role(monkeypatch, principal, allow_root):
    import boto3
    from botocore.stub import Stubber

    connection = Connection(region="ap-south-1", allow_root=allow_root)
    sts = boto3.client("sts", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test")
    ec2 = boto3.client("ec2", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test")
    base = Mock()
    base.client.side_effect = lambda name, **_: {"sts": sts, "ec2": ec2}[name]
    monkeypatch.setattr(connection, "base_session", lambda _: base)
    with Stubber(sts) as auth, Stubber(ec2) as inventory:
        auth.add_response("get_caller_identity", {"Arn": f"arn:aws:iam::123456789012:{principal}",
            "Account": "123456789012", "UserId": "test-user"}, {})
        inventory.add_response("describe_instances", {"Reservations": [{"Instances": [{
            "InstanceId": "i-0123456789abcdef0", "State": {"Name": "running"}}]}]}, {})
        result = Diagnostics(connection).discover()
        assert result["status"] == "ok"
        assert result["instances"][0]["region"] == "ap-south-1"
        auth.assert_no_pending_responses()
        inventory.assert_no_pending_responses()
