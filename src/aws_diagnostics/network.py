"""Optional public website DNS/TCP/TLS checks from the diagnostics host."""
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit


def website_target(url):
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
        raise ValueError("website_url must be http(s), without credentials or a fragment")
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("website port must be from 1 to 65535")
    return parts.hostname, port, parts.scheme == "https"


def probe_website(target):
    host, port, tls = target
    result = {"host": host, "port": port, "vantage_point": "diagnostics server",
              "http_request_sent": False, "checks": []}
    checks = result["checks"]
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        checks.append({"check": "dns", "status": "failed", "message": "DNS resolution failed from diagnostics server."})
        return result
    ips = sorted({a[4][0] for a in addresses})
    checks.append({"check": "dns", "status": "resolved", "addresses": ips})
    # Reject the complete answer set, preventing DNS rebinding to metadata/internal services.
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        checks.append({"check": "tcp", "status": "unknown", "message": "Only globally routable website addresses may be probed."})
        return result
    seen = set()
    for family, socktype, proto, _, address in addresses:
        if address[0] in seen:
            continue
        seen.add(address[0])
        if len(seen) > 4:
            checks.append({"check": "addresses", "status": "unknown", "message": "Probe capped at four addresses."})
            break
        try:
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(3)
                sock.connect(address)
                checks.append({"check": "tcp", "status": "connected", "address": address[0]})
                if tls:
                    try:
                        with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as secure:
                            checks.append({"check": "tls", "status": "verified", "address": address[0],
                                           "protocol": secure.version(), "certificate_expires": secure.getpeercert().get("notAfter")})
                    except (OSError, ssl.SSLError):
                        checks.append({"check": "tls", "status": "failed", "address": address[0],
                                       "message": "TLS handshake/certificate validation failed from diagnostics server."})
        except OSError:
            checks.append({"check": "tcp", "status": "failed", "address": address[0],
                           "message": "TCP connection failed from diagnostics server."})
    return result


def gateway_checks(instance, ec2, check, findings, gaps):
    vpc = instance.get("VpcId")
    if not vpc:
        return
    gateways = check("internet_gateways", lambda: ec2.describe_internet_gateways(
        Filters=[{"Name": "attachment.vpc-id", "Values": [vpc]}])["InternetGateways"])
    if gateways is None:
        return
    return gateways
