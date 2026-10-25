from datetime import datetime, timezone
from unittest.mock import Mock

import boto3
from botocore.stub import Stubber
from botocore.exceptions import ClientError, SSOTokenLoadError

from aws_diagnostics.auth import Connection, error_result
from aws_diagnostics.diagnostics import Diagnostics

ID = "i-0123456789abcdef0"


def client(service):
    return boto3.client(service, region_name="us-east-1", aws_access_key_id="test",
                        aws_secret_access_key="test")


def test_expired_session_is_reconnect_but_denied_is_not():
    assert error_result(SSOTokenLoadError(error_msg="secret"))["status"] == "reconnect_required"
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret"}}, "DescribeInstances")
    assert error_result(denied)["status"] == "error"
    assert "secret" not in str(error_result(denied))


def test_active_session_does_not_launch_login(monkeypatch):
    connection = Connection()
    monkeypatch.setattr(connection, "identity", lambda: {"status": "connected"})
    launch = Mock(side_effect=AssertionError("Login must not start"))
    monkeypatch.setattr("aws_diagnostics.auth.subprocess.Popen", launch)
    assert connection.connect()["status"] == "connected"
    launch.assert_not_called()


def test_cli_login_output_becomes_single_link():
    import io
    connection = Connection()
    process = Mock(stdout=io.StringIO("Open this URL:\nhttps://device.sso.us-east-1.amazonaws.com/\nABCD-EFGH\n"))
    # Inspect link while the process is about to complete.
    def finish():
        assert connection.login["connect_url"].endswith("?user_code=ABCD-EFGH")
        assert connection.login["status"] == "login_required"
        return 0
    process.wait.side_effect = finish
    connection._read_login(process)
    assert connection.login["status"] == "login_complete"


def test_discovery_follows_pagination():
    ec2 = client("ec2")
    connection = Mock(region="us-east-1")
    connection.session.return_value.client.return_value = ec2
    instance = {"InstanceId": ID, "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "web"}]}
    with Stubber(ec2) as stub:
        stub.add_response("describe_instances", {"Reservations": [{"Instances": [instance]}], "NextToken": "next"}, {})
        stub.add_response("describe_instances", {"Reservations": []}, {"NextToken": "next"})
        result = Diagnostics(connection).discover()
        assert result["instances"][0]["name"] == "web"
        stub.assert_no_pending_responses()


def test_diagnosis_reports_evidence_and_permission_gaps():
    ec2, ssm, cloudwatch = client("ec2"), client("ssm"), client("cloudwatch")
    connection = Mock(region="us-east-1")
    connection.session.return_value.client.side_effect = lambda name, **kw: {
        "ec2": ec2, "ssm": ssm, "cloudwatch": cloudwatch}[name]
    with Stubber(ec2) as e, Stubber(ssm) as s, Stubber(cloudwatch) as c:
        e.add_response("describe_instances", {"Reservations": [{"Instances": [{
            "InstanceId": ID, "State": {"Name": "running"},
            "SecurityGroups": [{"GroupId": "sg-12345678"}]}]}]}, {"InstanceIds": [ID]})
        e.add_response("describe_instance_status", {"InstanceStatuses": [{"InstanceId": ID,
            "SystemStatus": {"Status": "impaired"}}]}, {"InstanceIds": [ID], "IncludeAllInstances": True})
        e.add_client_error("describe_security_groups", "UnauthorizedOperation", expected_params={"GroupIds": ["sg-12345678"]})
        s.add_response("describe_instance_information", {"InstanceInformationList": []},
                       {"Filters": [{"Key": "InstanceIds", "Values": [ID]}]})
        c.add_response("get_metric_statistics", {"Datapoints": [{"Timestamp": datetime.now(timezone.utc), "Average": 95.0}]})
        result = Diagnostics(connection).diagnose(ID)
        assert result["status"] == "partial"
        assert len(result["findings"]) == 3
        assert result["unavailable_checks"][0]["check"] == "security_groups"
        assert result["changes_made"] is False
        for stub in (e, s, c):
            stub.assert_no_pending_responses()


def test_invalid_instance_never_calls_aws():
    connection = Mock()
    assert Diagnostics(connection).diagnose("web-server")["status"] == "invalid_input"
    connection.session.assert_not_called()


def test_expired_initial_request_is_saved_and_resumed(monkeypatch):
    from aws_diagnostics import server
    server.pending.clear()
    diagnose = Mock(side_effect=[{"status": "reconnect_required"}, {"status": "ok"}])
    monkeypatch.setattr(server.diagnostics, "diagnose", diagnose)
    result = server.diagnose_instance(ID, "us-east-1", "web down")
    request_id = result["request_id"]
    assert server.resume_diagnosis(request_id)["status"] == "ok"
    assert diagnose.call_args.args == (ID, "us-east-1", "web down", False, None, "unknown", 22, None)
    assert server.resume_diagnosis(request_id)["status"] == "not_found"


def test_repeated_expiry_keeps_pending_request(monkeypatch):
    from aws_diagnostics import server
    server.pending.clear()
    monkeypatch.setattr(server.diagnostics, "diagnose", Mock(return_value={"status": "reconnect_required"}))
    result = server.diagnose_instance(ID)
    assert server.resume_diagnosis(result["request_id"])["request_id"] == result["request_id"]


def test_missing_cli_has_clear_setup_error(monkeypatch):
    connection = Connection()
    monkeypatch.setattr(connection, "identity", lambda: {"status": "reconnect_required"})
    monkeypatch.setattr("aws_diagnostics.auth.find_aws", lambda: None)
    assert connection.connect()["status"] == "setup_required"


def test_pending_login_is_reused(monkeypatch):
    connection = Connection()
    connection.process = Mock()
    connection.process.poll.return_value = None
    connection.login = {"status": "login_required", "connect_url": "https://example.invalid"}
    monkeypatch.setattr(connection, "identity", lambda: {"status": "reconnect_required"})
    assert connection.connect() == connection.login
    connection.close()
    connection.process.terminate.assert_called_once()
    connection.process = None


def test_expiry_during_partial_diagnosis_can_resume(monkeypatch):
    from aws_diagnostics import server
    server.pending.clear()
    monkeypatch.setattr(server.diagnostics, "diagnose", Mock(return_value={
        "status": "partial", "reconnect_required": True, "evidence": {"health": []}}))
    result = server.diagnose_instance(ID)
    assert result["evidence"] == {"health": []}
    assert result["request_id"] in server.pending


def test_main_route_table_fallback_and_no_cpu_data():
    ec2, ssm, cloudwatch = client("ec2"), client("ssm"), client("cloudwatch")
    connection = Mock(region="us-east-1")
    connection.session.return_value.client.side_effect = lambda name, **kw: {
        "ec2": ec2, "ssm": ssm, "cloudwatch": cloudwatch}[name]
    with Stubber(ec2) as e, Stubber(ssm) as s, Stubber(cloudwatch) as c:
        e.add_response("describe_instances", {"Reservations": [{"Instances": [{
            "InstanceId": ID, "State": {"Name": "stopped"},
            "SubnetId": "subnet-12345678", "VpcId": "vpc-12345678"}]}]}, {"InstanceIds": [ID]})
        e.add_response("describe_instance_status", {"InstanceStatuses": []})
        e.add_response("describe_network_acls", {"NetworkAcls": []},
                       {"Filters": [{"Name": "association.subnet-id", "Values": ["subnet-12345678"]}]})
        e.add_response("describe_route_tables", {"RouteTables": []},
                       {"Filters": [{"Name": "association.subnet-id", "Values": ["subnet-12345678"]}]})
        e.add_response("describe_route_tables", {"RouteTables": [{"RouteTableId": "rtb-12345678"}]},
                       {"Filters": [{"Name": "vpc-id", "Values": ["vpc-12345678"]},
                                    {"Name": "association.main", "Values": ["true"]}]})
        s.add_client_error("describe_instance_information", "ExpiredTokenException")
        c.add_response("get_metric_statistics", {"Datapoints": []})
        result = Diagnostics(connection).diagnose(ID, issue="SSH unavailable", source_ip="203.0.113.9", connection_path="public")
        assert result["reconnect_required"] is True
        assert result["ssh_analysis"]["status"] == "blocked"
        assert any(f.get("check") == "public_ipv4" for f in result["findings"])
        assert result["evidence"]["routes"][0]["RouteTableId"] == "rtb-12345678"
        assert {"check": "cpu_last_30_minutes", "status": "no_data"} in result["unavailable_checks"]
        assert "stopped" in result["findings"][0]["finding"]
        for stub in (e, s, c):
            stub.assert_no_pending_responses()
