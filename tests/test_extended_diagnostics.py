import json
import socket
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from aws_diagnostics.auth import Connection, error_result
from aws_diagnostics.diagnostics import Diagnostics
from aws_diagnostics.network import probe_website, website_target
from aws_diagnostics.policy import READ_ACTIONS, SESSION_POLICY
from aws_diagnostics.ssm import collect_ssm
from aws_diagnostics.telemetry import collect_application, collect_metrics


ID = "i-0123456789abcdef0"
NOW = datetime.now(timezone.utc)


def capture():
    evidence, findings, gaps = {}, [], []

    def check(key, operation):
        evidence[key] = operation()
        return evidence[key]

    return check, evidence, findings, gaps


@pytest.mark.parametrize("principal,role", [
    ("arn:aws:iam::123456789012:root", "arn:aws:iam::123456789012:role/Diagnostics"),
    ("arn:aws:iam::123456789012:user/test", ""),
    ("arn:aws:iam::123456789012:user/test", "invalid-role"),
])
def test_security_boundary_fails_closed(monkeypatch, principal, role):
    monkeypatch.setenv("AWS_DIAGNOSTICS_ROLE_ARN", role)
    base = Mock()
    base.client.return_value.get_caller_identity.return_value = {"Arn": principal}
    connection = Connection(access_mode="role")
    monkeypatch.setattr(connection, "base_session", lambda _: base)
    with pytest.raises(ClientError) as exc:
        connection.session()
    assert error_result(exc.value)["status"] == "security_setup_required"
    base.client.return_value.assume_role.assert_not_called()


@pytest.mark.parametrize("role,reason", [("", "missing_role_arn"), ("invalid", "invalid_role_arn")])
def test_role_configuration_error_precedes_login_and_aws_calls(monkeypatch, role, reason):
    monkeypatch.setenv("AWS_DIAGNOSTICS_ROLE_ARN", role)
    connection = Connection(access_mode="role")
    base = Mock(side_effect=AssertionError("Must not inspect credentials before role configuration"))
    launch = Mock(side_effect=AssertionError("Must not start another login"))
    monkeypatch.setattr(connection, "base_session", base)
    monkeypatch.setattr("aws_diagnostics.auth.subprocess.Popen", launch)
    result = connection.connect()
    assert result["status"] == "security_setup_required"
    assert result["reason"] == reason
    base.assert_not_called()
    launch.assert_not_called()


@pytest.mark.parametrize("duration,expected", [(None, 3600), ("900", 900), ("7200", 7200), ("43200", 43200)])
def test_only_restricted_role_credentials_reach_diagnostic_session(monkeypatch, duration, expected):
    monkeypatch.delenv("AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS", raising=False)
    if duration is not None:
        monkeypatch.setenv("AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS", duration)
    monkeypatch.setenv("AWS_DIAGNOSTICS_ROLE_ARN", "arn:aws:iam::123456789012:role/Diagnostics")
    base = Mock()
    sts = base.client.return_value
    sts.get_caller_identity.return_value = {"Arn": "arn:aws:iam::123456789012:user/test"}
    sts.assume_role.return_value = {"Credentials": {
        "AccessKeyId": "restricted-id", "SecretAccessKey": "restricted-secret", "SessionToken": "restricted-token"}}
    connection = Connection()
    monkeypatch.setattr(connection, "base_session", lambda _: base)
    factory = Mock()
    monkeypatch.setattr("aws_diagnostics.auth.boto3.Session", factory)
    connection.session("us-west-2")
    assert sts.assume_role.call_args.kwargs["DurationSeconds"] == expected
    assert json.loads(sts.assume_role.call_args.kwargs["Policy"]) == SESSION_POLICY
    deny = next(s for s in SESSION_POLICY["Statement"] if s["Effect"] == "Deny")
    assert deny["NotAction"] == READ_ACTIONS
    assert "ssm:SendCommand" not in READ_ACTIONS
    assert "sts:AssumeRole" not in READ_ACTIONS
    assert factory.call_args.kwargs["aws_access_key_id"] == "restricted-id"
    assert factory.call_args.kwargs["region_name"] == "us-west-2"

    # Later calls obtain another restricted session without starting browser login.
    sts.assume_role.return_value["Credentials"]["AccessKeyId"] = "renewed-restricted-id"
    connection.session("us-west-2")
    assert sts.assume_role.call_count == 2
    assert factory.call_args.kwargs["aws_access_key_id"] == "renewed-restricted-id"
    assert json.loads(sts.assume_role.call_args.kwargs["Policy"]) == SESSION_POLICY
    assert connection.process is None


@pytest.mark.parametrize("duration", ["", "invalid", "3600.5", "899", "43201"])
def test_invalid_role_session_duration_rejected(monkeypatch, duration):
    monkeypatch.setenv("AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS", duration)
    with pytest.raises(ValueError, match="AWS_DIAGNOSTICS_SESSION_DURATION_SECONDS"):
        Connection()


def test_failed_assume_role_never_falls_back(monkeypatch):
    monkeypatch.setenv("AWS_DIAGNOSTICS_ROLE_ARN", "arn:aws:iam::123456789012:role/Diagnostics")
    base = Mock()
    base.client.return_value.get_caller_identity.return_value = {"Arn": "arn:aws:iam::123456789012:user/test"}
    base.client.return_value.assume_role.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "AssumeRole")
    connection = Connection()
    monkeypatch.setattr(connection, "base_session", lambda _: base)
    with pytest.raises(ClientError):
        connection.session()


def test_example_policy_matches_enforced_boundary():
    example = Path(__file__).resolve().parents[1] / "examples" / "read-only-policy.json"
    assert json.loads(example.read_text()) == SESSION_POLICY
    assert len(json.dumps(SESSION_POLICY, separators=(",", ":"))) <= 2048


def test_network_metrics_credits_and_latency_use_matching_time_buckets():
    cw = Mock()
    def metric(**args):
        name = args["MetricName"]
        value = {"VolumeTotalReadTime": 2, "VolumeReadOps": 4,
                 "VolumeTotalWriteTime": 9, "VolumeWriteOps": 0}.get(name, 0)
        return {"Datapoints": [{"Timestamp": NOW, "Sum": value, "Minimum": 0}]}
    cw.get_metric_statistics.side_effect = metric
    check, evidence, findings, gaps = capture()
    collect_metrics({"InstanceId": ID, "InstanceType": "t3.micro", "BlockDeviceMappings": [
        {"Ebs": {"VolumeId": "vol-1"}}]}, cw, NOW, check, evidence, findings, gaps)
    assert evidence["metrics.vol-1.read_latency_ms"][0]["milliseconds_per_operation"] == 500
    assert evidence["metrics.vol-1.write_latency_ms"] == []
    assert "metrics.NetworkIn" in evidence and "metrics.CPUCreditBalance" in evidence
    assert findings[0]["confidence"] == "medium"


def test_application_telemetry_retains_filesystem_dimensions_and_bounds_logs():
    cw, logs = Mock(), Mock()
    cw.list_metrics.side_effect = lambda **kw: {"Metrics": [{"Dimensions": [
        {"Name": "InstanceId", "Value": ID}, {"Name": "path", "Value": "/data"}]}]}
    cw.get_metric_statistics.return_value = {"Datapoints": [{"Timestamp": NOW, "Maximum": 95, "Minimum": 0}]}
    logs.get_log_events.return_value = {"events": [{"message": "example"}], "nextForwardToken": "hidden"}
    check, evidence, findings, gaps = capture()
    collect_application(ID, cw, logs, NOW, check, findings, gaps, "app", "instance-stream")
    assert len(findings) == 3
    assert findings[0]["dimensions"][1]["Value"] == "/data"
    assert logs.get_log_events.call_args.kwargs["limit"] == 100
    assert logs.get_log_events.call_args.kwargs["logStreamName"] == "instance-stream"
    assert "nextForwardToken" not in evidence["application.logs"]


def test_missing_app_telemetry_is_unknown_not_healthy():
    cw = Mock()
    cw.list_metrics.return_value = {"Metrics": []}
    check, evidence, findings, gaps = capture()
    collect_application(ID, cw, None, NOW, check, findings, gaps, None, None)
    assert not findings
    assert len([g for g in gaps if g["status"] == "no_data"]) == 3
    assert any(g["check"] == "application.services" for g in gaps)


def test_ssm_role_denial_and_agent_unknown_remain_distinct():
    session, ec2 = Mock(), Mock()
    iam = session.client.return_value
    iam.get_instance_profile.return_value = {"InstanceProfile": {"Roles": [
        {"RoleName": "test", "Arn": "arn:aws:iam::123456789012:role/test"}]}}
    iam.simulate_principal_policy.return_value = {"EvaluationResults": [
        {"EvalActionName": "ssm:UpdateInstanceInformation", "EvalDecision": "implicitDeny"}]}
    ec2.describe_vpc_endpoints.return_value = {"VpcEndpoints": []}
    check, evidence, findings, gaps = capture()
    collect_ssm({"IamInstanceProfile": {"Arn": "arn:aws:iam::123456789012:instance-profile/test"},
                 "VpcId": "vpc-test"}, session, ec2, "us-east-1", check, findings, gaps)
    assert "simulation" in findings[0]["finding"]
    assert findings[0]["confidence"] == "medium"
    assert any(g["check"] == "ssm.agent" and g["status"] == "unknown" for g in gaps)
    assert not any("endpoint" in f["finding"] for f in findings)


@pytest.mark.parametrize("url", ["file:///tmp/test", "https://user:pass@example.com", "http://example.com:99999"])
def test_invalid_website_input(url):
    with pytest.raises(ValueError):
        website_target(url)


def test_dns_answer_containing_private_ip_never_connects(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))])
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(socket, "socket", connect)
    result = probe_website(website_target("http://example.com"))
    assert result["checks"][-1]["status"] == "unknown"
    connect.assert_not_called()


def test_tls_failure_is_separate_from_successful_tcp(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    from unittest.mock import MagicMock
    sock = MagicMock()
    monkeypatch.setattr(socket, "socket", lambda *a: sock)
    context = Mock()
    context.wrap_socket.side_effect = OSError("certificate failed")
    monkeypatch.setattr("aws_diagnostics.network.ssl.create_default_context", lambda: context)
    result = probe_website(website_target("https://example.com"))
    assert [c["status"] for c in result["checks"]] == ["resolved", "connected", "failed"]
    sock.__enter__.return_value.connect.assert_called_once_with(("8.8.8.8", 443))


@pytest.mark.parametrize("options", [{"app_checks": False, "log_group": "app", "log_stream": "stream"},
    {"app_checks": True, "log_group": "app"}, {"website_url": "file:///test"},
    {"website_url": "https://example.com:0"}])
def test_invalid_optional_checks_never_contact_aws(options):
    connection = Mock()
    assert Diagnostics(connection).diagnose(ID, **options)["status"] == "invalid_input"
    connection.session.assert_not_called()


def test_new_options_survive_reconnect(monkeypatch):
    from aws_diagnostics import server
    mocked = Mock(side_effect=[{"status": "reconnect_required"}, {"status": "ok"}])
    monkeypatch.setattr(server.diagnostics, "diagnose", mocked)
    result = server.diagnose_instance(ID, app_checks=True, log_group="app", log_stream="stream",
                                      website_url="https://example.com")
    server.resume_diagnosis(result["request_id"])
    assert mocked.call_args.args[8:] == (True, "app", "stream", "https://example.com")


def test_website_port_and_gateway_evidence_in_full_report(monkeypatch):
    connection = Mock(region="us-east-1")
    ec2, ssm, cw = Mock(), Mock(), Mock()
    connection.session.return_value.client.side_effect = lambda name, **kw: {"ec2": ec2, "ssm": ssm, "cloudwatch": cw}[name]
    instance = {"InstanceId": ID, "State": {"Name": "running"}, "PublicIpAddress": "8.8.8.8",
                "SubnetId": "subnet-test", "VpcId": "vpc-test", "SecurityGroups": [{"GroupId": "sg-test"}]}
    ec2.describe_instances.return_value = {"Reservations": [{"Instances": [instance]}]}
    ec2.describe_instance_status.return_value = {"InstanceStatuses": []}
    ec2.describe_security_groups.return_value = {"SecurityGroups": [{"GroupId": "sg-test", "IpPermissions": []}]}
    ec2.describe_network_acls.return_value = {"NetworkAcls": []}
    ec2.describe_route_tables.return_value = {"RouteTables": [{"Routes": [
        {"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-missing", "State": "active"}]}]}
    ec2.describe_internet_gateways.return_value = {"InternetGateways": []}
    ssm.describe_instance_information.return_value = {"InstanceInformationList": [{"PingStatus": "Online"}]}
    cw.get_metric_statistics.return_value = {"Datapoints": []}
    monkeypatch.setattr("aws_diagnostics.diagnostics.probe_website", lambda _: {
        "checks": [{"check": "dns", "status": "resolved", "addresses": ["8.8.8.8"]}]})
    result = Diagnostics(connection).diagnose(ID, website_url="https://example.com:8443",
        source_ip="203.0.113.9", connection_path="public")
    assert result["ssh_analysis"] is None
    assert result["evidence"]["website_configuration"]["website_port"] == 8443
    assert any("TCP 8443" in f["finding"] for f in result["findings"])
    assert any("gateway" in f["finding"] for f in result["findings"])
    assert all("confidence" in f and "missing_checks" in f for f in result["findings"])
