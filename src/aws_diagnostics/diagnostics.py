import re
from datetime import datetime, timedelta, timezone

from botocore.exceptions import BotoCoreError, ClientError
from .auth import CLIENT_CONFIG, error_result
from .ssh import analyze_ssh, validate_context


def summarize(instance, region):
    return {"instance_id": instance["InstanceId"], "region": region,
            "name": next((t["Value"] for t in instance.get("Tags", []) if t["Key"] == "Name"), ""),
            "state": instance["State"]["Name"], "type": instance.get("InstanceType"),
            "private_ip": instance.get("PrivateIpAddress"), "public_ip": instance.get("PublicIpAddress")}


class Diagnostics:
    def __init__(self, connection):
        self.connection = connection

    def discover(self, region=None, name=None):
        region = region or self.connection.region
        try:
            ec2 = self.connection.session(region).client("ec2", config=CLIENT_CONFIG)
            kwargs = {"Filters": [{"Name": "tag:Name", "Values": [name]}]} if name else {}
            instances = []
            for page in ec2.get_paginator("describe_instances").paginate(**kwargs):
                for reservation in page["Reservations"]:
                    instances.extend(summarize(i, region) for i in reservation["Instances"])
            return {"status": "ok", "region": region, "instances": instances,
                    "message": "Select an instance ID and region. Names may not be unique."}
        except (BotoCoreError, ClientError) as error:
            return error_result(error)

    def regions(self):
        try:
            ec2 = self.connection.session().client("ec2", config=CLIENT_CONFIG)
            return {"status": "ok", "regions": sorted(r["RegionName"] for r in ec2.describe_regions()["Regions"])}
        except (BotoCoreError, ClientError) as error:
            return error_result(error)

    def diagnose(self, instance_id, region=None, issue="", ssh=False, source_ip=None,
                 connection_path="unknown", ssh_port=22, client_port=None):
        ssh = ssh or bool(re.search(r"\bssh\b", issue, re.IGNORECASE)) or source_ip is not None
        try:
            validate_context(source_ip, connection_path, ssh_port, client_port)
        except ValueError as error:
            return {"status": "invalid_input", "message": str(error)}
        if not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", instance_id):
            return {"status": "invalid_input", "message": "Choose an exact EC2 instance ID from discover_instances."}
        region = region or self.connection.region
        try:
            session = self.connection.session(region)
            ec2 = session.client("ec2", config=CLIENT_CONFIG)
            reservations = ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
            instances = [i for r in reservations for i in r["Instances"]]
            if not instances:
                return {"status": "not_found", "instance_id": instance_id, "region": region}
            instance = instances[0]
        except (BotoCoreError, ClientError) as error:
            return error_result(error)

        evidence, findings, gaps = {}, [], []

        def check(name, operation):
            try:
                evidence[name] = operation()
                return evidence[name]
            except (BotoCoreError, ClientError) as error:
                gaps.append({"check": name, **error_result(error)})
                return None

        state = instance["State"]["Name"]
        if state != "running":
            findings.append({"finding": f"Instance state is {state}.", "evidence": "instance.state",
                             "recommendation": "Confirm intended instance state before considering any start/recovery action."})
        health = check("health", lambda: ec2.describe_instance_status(
            InstanceIds=[instance_id], IncludeAllInstances=True)["InstanceStatuses"])
        for status in health or []:
            for key in ("SystemStatus", "InstanceStatus", "AttachedEbsStatus"):
                if status.get(key, {}).get("Status") == "impaired":
                    findings.append({"finding": f"{key} is impaired.", "evidence": f"health.{key}",
                                     "recommendation": "Inspect AWS status details and recovery options before making changes."})
        groups = [g["GroupId"] for g in instance.get("SecurityGroups", [])]
        if groups:
            check("security_groups", lambda: ec2.describe_security_groups(GroupIds=groups)["SecurityGroups"])
        subnet = instance.get("SubnetId")
        if subnet:
            check("network_acls", lambda: ec2.describe_network_acls(
                Filters=[{"Name": "association.subnet-id", "Values": [subnet]}])["NetworkAcls"])

            def routes():
                explicit = ec2.describe_route_tables(Filters=[{"Name": "association.subnet-id", "Values": [subnet]}])["RouteTables"]
                return explicit or ec2.describe_route_tables(Filters=[
                    {"Name": "vpc-id", "Values": [instance["VpcId"]]},
                    {"Name": "association.main", "Values": ["true"]}])["RouteTables"]

            check("routes", routes)
        ssm = check("ssm", lambda: session.client("ssm", config=CLIENT_CONFIG).describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}])["InstanceInformationList"])
        if ssm is not None and not any(i.get("PingStatus") == "Online" for i in ssm):
            findings.append({"finding": "SSM does not report this instance online.", "evidence": "ssm",
                             "recommendation": "Check SSM agent, instance IAM role and SSM endpoint connectivity."})
        end = datetime.now(timezone.utc)

        def cpu():
            points = session.client("cloudwatch", config=CLIENT_CONFIG).get_metric_statistics(
                Namespace="AWS/EC2", MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=end - timedelta(minutes=30), EndTime=end, Period=300,
                Statistics=["Average", "Maximum"])["Datapoints"]
            return sorted(points, key=lambda p: p["Timestamp"])

        points = check("cpu_last_30_minutes", cpu)
        if points and any(p.get("Average", 0) >= 90 for p in points):
            findings.append({"finding": "CPU average reached at least 90% in a five-minute interval.",
                             "evidence": "cpu_last_30_minutes", "recommendation": "Investigate workload and process CPU usage; this alone does not prove root cause."})
        if points == []:
            gaps.append({"check": "cpu_last_30_minutes", "status": "no_data"})
        ssh_analysis = analyze_ssh(instance, evidence, source_ip, connection_path, ssh_port, client_port) if ssh else None
        if ssh_analysis:
            findings.extend(ssh_analysis["findings"])
            gaps.extend({"check": "ssh." + c["check"], "status": "unknown", "message": c["message"]}
                        for c in ssh_analysis["checks"] if c["status"] == "unknown")
        return {"status": "partial" if gaps else "ok", "instance": summarize(instance, region),
                "ssh_analysis": ssh_analysis,
                "issue": issue, "checked_at": end.isoformat(), "findings": findings,
                "evidence": evidence, "unavailable_checks": gaps,
                "reconnect_required": any(g["status"] == "reconnect_required" for g in gaps),
                "limitations": ["No OS commands or application logs collected in this version.",
                                "Network configuration is evidence, not a full reachability test.",
                                "No findings does not prove the application is healthy."],
                "changes_made": False}
