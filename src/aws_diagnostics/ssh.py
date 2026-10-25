"""Conservative IPv4 SSH configuration analysis; no probes or AWS writes."""
from ipaddress import ip_address, ip_network


def validate_context(source_ip, connection_path, ssh_port, client_port):
    if connection_path not in {"unknown", "public", "private"}:
        raise ValueError("connection_path must be unknown, public, or private")
    if source_ip and ip_address(source_ip).version != 4:
        raise ValueError("SSH analysis currently supports IPv4 source addresses only")
    for name, value in (("ssh_port", ssh_port), ("client_port", client_port)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535):
            raise ValueError(f"{name} must be an integer from 1 to 65535")


def contains(cidr, address):
    return bool(cidr and address and ip_address(address) in ip_network(cidr))


def tcp_port(rule, port, acl=False):
    protocol = str(rule.get("Protocol" if acl else "IpProtocol"))
    if protocol == "-1":
        return True
    ports = rule.get("PortRange", {}) if acl else {"From": rule.get("FromPort"), "To": rule.get("ToPort")}
    return protocol in {"6", "tcp"} and ports.get("From") is not None and ports.get("To") is not None and ports["From"] <= port <= ports["To"]


def analyze_ssh(instance, evidence, source_ip=None, connection_path="unknown", ssh_port=22, client_port=None):
    validate_context(source_ip, connection_path, ssh_port, client_port)
    checks = []

    def add(check, status, message, reference, **details):
        checks.append({"check": check, "status": status, "message": message,
                       "evidence": reference, **details})

    if len(instance.get("NetworkInterfaces", [])) > 1:
        add("target", "unknown", "Multiple network interfaces: select and inspect the target ENI before drawing SSH conclusions.", "instance.NetworkInterfaces")
        return {"status": "incomplete", "checks": checks, "findings": [], "end_to_end_verified": False}

    if connection_path == "public":
        add("public_ipv4", "present" if instance.get("PublicIpAddress") else "blocked",
            "Public IPv4 is present." if instance.get("PublicIpAddress") else
            "No public IPv4 is assigned; direct internet IPv4 SSH to this instance is unavailable.", "instance.public_ip")
    else:
        add("public_ipv4", "not_applicable" if connection_path == "private" else "unknown",
            "A public IPv4 is not required for a private path." if connection_path == "private" else
            "Specify public or private connection path; missing public IP alone is not a fault.", "instance.public_ip")

    groups = evidence.get("security_groups")
    expected_groups = {g["GroupId"] for g in instance.get("SecurityGroups", [])}
    complete_groups = bool(expected_groups) and expected_groups <= {g.get("GroupId") for g in groups or []}
    if not complete_groups:
        add("security_groups", "unknown", "Security-group evidence is missing or incomplete.", "security_groups")
    else:
        candidates = [(g, r) for g in groups for r in g.get("IpPermissions", []) if tcp_port(r, ssh_port)]
        matched = [(g, r) for g, r in candidates if any(contains(c.get("CidrIp"), source_ip) for c in r.get("IpRanges", []))]
        unresolved = any(r.get("UserIdGroupPairs") or r.get("PrefixListIds") for _, r in candidates)
        if matched:
            add("security_groups", "allowed", f"Security groups allow TCP {ssh_port} from {source_ip}.", "security_groups",
                matching_groups=sorted({g["GroupId"] for g, _ in matched}))
        elif not source_ip:
            add("security_groups", "unknown", "Provide the source IPv4 seen by the instance (client egress, VPN, or bastion IP).", "security_groups")
        elif unresolved:
            add("security_groups", "unknown", "No matching IPv4 CIDR rule; source-group or prefix-list references need resolution.", "security_groups")
        else:
            add("security_groups", "blocked", f"Attached security groups do not allow TCP {ssh_port} from {source_ip}.", "security_groups")

    tables = evidence.get("routes")
    if not source_ip or not tables or len(tables) != 1:
        add("return_route", "unknown", "Source IPv4 and one effective route table are required.", "routes")
    elif any(r.get("DestinationPrefixListId") for r in tables[0].get("Routes", [])):
        add("return_route", "unknown", "Prefix-list routes require resolution before selecting the return route.", "routes")
    else:
        candidates = [r for r in tables[0].get("Routes", []) if contains(r.get("DestinationCidrBlock"), source_ip)]
        if not candidates:
            add("return_route", "blocked", f"No IPv4 return route matches {source_ip}.", "routes")
        else:
            longest = max(ip_network(r["DestinationCidrBlock"]).prefixlen for r in candidates)
            selected = [r for r in candidates if ip_network(r["DestinationCidrBlock"]).prefixlen == longest]
            if len(selected) != 1:
                add("return_route", "unknown", "Equal-prefix route selection requires further inspection.", "routes")
            else:
                route = selected[0]
                if route.get("State") == "blackhole":
                    status, message = "blocked", "The most specific return route is a blackhole."
                elif route.get("State") != "active":
                    status, message = "unknown", "Return route state is unavailable."
                elif connection_path == "public" and not route.get("GatewayId", "").startswith("igw-"):
                    status, message = "unknown", "Return route is not directly through an internet gateway; direct internet SSH path needs investigation."
                else:
                    status, message = "present", "A matching active return route exists; gateway attachment and the complete path are not verified."
                add("return_route", status, message, "routes", selected_route=route)

    acls = evidence.get("network_acls")
    for egress, port, name in ((False, ssh_port, "acl_inbound"), (True, client_port, "acl_return")):
        if connection_path != "public":
            add(name, "unknown", "Private/unknown path: establish whether traffic crosses the subnet boundary before evaluating ACLs.", "network_acls")
        elif not source_ip or not acls or len(acls) != 1:
            add(name, "unknown", "Source IPv4 and subnet ACL evidence are required.", "network_acls")
        elif port is None:
            add(name, "unknown", "Provide the client's actual TCP source port to check the return path; it is not normally port 22.", "network_acls")
        else:
            entries = sorted(acls[0].get("Entries", []), key=lambda r: r["RuleNumber"])
            match = next((r for r in entries if r.get("Egress") == egress and
                          contains(r.get("CidrBlock"), source_ip) and tcp_port(r, port, acl=True)), None)
            if not entries:
                add(name, "unknown", "ACL entries are unavailable.", "network_acls")
            else:
                allowed = bool(match and match.get("RuleAction") == "allow")
                add(name, "allowed" if allowed else "blocked",
                    f"Subnet ACL {'allows' if allowed else 'denies'} {'return' if egress else 'inbound'} TCP destination port {port} for {source_ip}.",
                    "network_acls", rule_number=match["RuleNumber"] if match else "implicit deny")
    findings = [{"finding": c["message"], "evidence": c["evidence"], "check": c["check"],
                 "recommendation": "Review this rule/address/path against the intended SSH connection; no change was made."}
                for c in checks if c["status"] == "blocked"]
    return {"status": "blocked" if findings else "incomplete" if any(c["status"] == "unknown" for c in checks) else "no_blocker_found",
            "source_ip": source_ip, "connection_path": connection_path, "ssh_port": ssh_port,
            "client_port": client_port, "checks": checks, "findings": findings, "end_to_end_verified": False}
