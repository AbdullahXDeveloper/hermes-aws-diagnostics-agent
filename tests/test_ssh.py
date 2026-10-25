from copy import deepcopy
from unittest.mock import Mock

import pytest

from aws_diagnostics.ssh import analyze_ssh
from aws_diagnostics.diagnostics import Diagnostics

SOURCE = "203.0.113.9"
INSTANCE = {"PublicIpAddress": "198.51.100.5", "SecurityGroups": [{"GroupId": "sg-one"}]}


def evidence():
    return {
        "security_groups": [{"GroupId": "sg-one", "IpPermissions": [{
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "IpRanges": [{"CidrIp": "203.0.113.0/24"}]}]}],
        "routes": [{"Routes": [{"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-one", "State": "active"}]}],
        "network_acls": [{"Entries": [
            {"RuleNumber": 100, "Egress": False, "Protocol": "6", "PortRange": {"From": 22, "To": 22}, "CidrBlock": "0.0.0.0/0", "RuleAction": "allow"},
            {"RuleNumber": 100, "Egress": True, "Protocol": "6", "PortRange": {"From": 1024, "To": 65535}, "CidrBlock": "0.0.0.0/0", "RuleAction": "allow"}
        ]}]
    }


def check(result, name):
    return next(c for c in result["checks"] if c["check"] == name)


def analyze(data=None, instance=None, **kwargs):
    return analyze_ssh(INSTANCE if instance is None else instance, evidence() if data is None else data,
                       source_ip=SOURCE, connection_path="public", **kwargs)


def test_allowed_configuration_does_not_claim_reachability():
    result = analyze(client_port=54321)
    assert result["status"] == "no_blocker_found"
    assert result["end_to_end_verified"] is False


def test_source_cidr_mismatch_is_specific_blocker():
    data = evidence()
    data["security_groups"][0]["IpPermissions"][0]["IpRanges"] = [{"CidrIp": "192.0.2.0/24"}]
    result = analyze(data)
    assert check(result, "security_groups")["status"] == "blocked"
    assert SOURCE in result["findings"][0]["finding"]


def test_any_attached_security_group_can_allow():
    data = evidence()
    data["security_groups"].insert(0, {"GroupId": "sg-two", "IpPermissions": []})
    instance = deepcopy(INSTANCE)
    instance["SecurityGroups"].append({"GroupId": "sg-two"})
    assert check(analyze(data, instance), "security_groups")["status"] == "allowed"


def test_missing_group_or_reference_never_becomes_false_block():
    assert check(analyze({}), "security_groups")["status"] == "unknown"
    data = evidence()
    rule = data["security_groups"][0]["IpPermissions"][0]
    rule["IpRanges"] = []
    rule["UserIdGroupPairs"] = [{"GroupId": "sg-bastion"}]
    assert check(analyze(data), "security_groups")["status"] == "unknown"


def test_public_ip_required_only_for_public_path():
    instance = {"SecurityGroups": INSTANCE["SecurityGroups"]}
    assert check(analyze(instance=instance), "public_ipv4")["status"] == "blocked"
    result = analyze_ssh(instance, evidence(), SOURCE, "private")
    assert check(result, "public_ipv4")["status"] == "not_applicable"
    assert check(result, "acl_inbound")["status"] == "unknown"


def test_no_source_does_not_guess_client_ip():
    result = analyze_ssh(INSTANCE, evidence())
    assert check(result, "security_groups")["status"] == "unknown"
    assert not result["findings"]


def test_acl_lowest_rule_number_wins_regardless_of_list_order():
    data = evidence()
    data["network_acls"][0]["Entries"].append({"RuleNumber": 50, "Egress": False,
        "Protocol": "-1", "CidrBlock": SOURCE + "/32", "RuleAction": "deny"})
    result = analyze(data, client_port=54321)
    assert check(result, "acl_inbound")["status"] == "blocked"
    assert check(result, "acl_inbound")["rule_number"] == 50


def test_acl_return_uses_client_port_not_ssh_port():
    data = evidence()
    data["network_acls"][0]["Entries"][1]["PortRange"] = {"From": 22, "To": 22}
    assert check(analyze(data, client_port=54321), "acl_return")["status"] == "blocked"
    assert check(analyze(data), "acl_return")["status"] == "unknown"


def test_more_specific_blackhole_wins_over_default_route():
    data = evidence()
    data["routes"][0]["Routes"].append({"DestinationCidrBlock": SOURCE + "/32", "State": "blackhole"})
    result = analyze(data)
    assert check(result, "return_route")["status"] == "blocked"
    assert "blackhole" in check(result, "return_route")["message"]


def test_prefix_list_routes_and_multiple_interfaces_are_unknown():
    data = evidence()
    data["routes"][0]["Routes"].append({"DestinationPrefixListId": "pl-one", "State": "active"})
    assert check(analyze(data), "return_route")["status"] == "unknown"
    instance = deepcopy(INSTANCE)
    instance["NetworkInterfaces"] = [{}, {}]
    assert analyze(instance=instance)["status"] == "incomplete"


def test_custom_ssh_port_and_all_protocol_rule():
    assert check(analyze(ssh_port=2222), "security_groups")["status"] == "blocked"
    data = evidence()
    data["security_groups"][0]["IpPermissions"][0]["IpProtocol"] = "-1"
    assert check(analyze(data, ssh_port=2222), "security_groups")["status"] == "allowed"


@pytest.mark.parametrize("kwargs", [{"source_ip": "invalid"}, {"source_ip": "2001:db8::1"},
    {"connection_path": "auto"}, {"ssh_port": 0}, {"client_port": 70000}])
def test_invalid_context_rejected_before_aws_calls(kwargs):
    connection = Mock()
    result = Diagnostics(connection).diagnose("i-0123456789abcdef0", ssh=True, **kwargs)
    assert result["status"] == "invalid_input"
    connection.session.assert_not_called()


def test_ssh_context_preserved_across_reconnect(monkeypatch):
    from aws_diagnostics import server
    server.pending.clear()
    mocked = Mock(side_effect=[{"status": "reconnect_required"}, {"status": "ok"}])
    monkeypatch.setattr(server.diagnostics, "diagnose", mocked)
    result = server.diagnose_instance("i-0123456789abcdef0", ssh=True, source_ip=SOURCE,
                                      connection_path="public", ssh_port=2222, client_port=55555)
    server.resume_diagnosis(result["request_id"])
    assert mocked.call_args.args[3:] == (True, SOURCE, "public", 2222, 55555, False, None, None, None)
