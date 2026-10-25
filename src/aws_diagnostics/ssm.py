"""Separate known configuration facts from unobservable SSM agent state."""
from .auth import CLIENT_CONFIG


def collect_ssm(instance, session, ec2, region, check, findings, gaps):
    profile = instance.get("IamInstanceProfile", {}).get("Arn")
    if not profile:
        findings.append({"finding": "No instance IAM profile is attached; instance-profile SSM authentication is unavailable.",
                         "evidence": "instance.IamInstanceProfile", "confidence": "high",
                         "recommendation": "Review the intended SSM credential mode. Default Host Management Configuration can provide an alternative."})
    else:
        iam = session.client("iam", config=CLIENT_CONFIG)
        data = check("ssm_instance_profile", lambda: iam.get_instance_profile(
            InstanceProfileName=profile.rsplit("/", 1)[-1])["InstanceProfile"])
        for role in (data or {}).get("Roles", []):
            actions = ["ssm:UpdateInstanceInformation", "ssmmessages:CreateControlChannel",
                       "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
            key = "ssm_role_permissions." + role["RoleName"]
            result = check(key, lambda: iam.simulate_principal_policy(
                PolicySourceArn=role["Arn"], ActionNames=actions)["EvaluationResults"])
            for item in result or []:
                if item.get("EvalDecision") != "allowed":
                    findings.append({"finding": f"Instance-role simulation did not allow {item['EvalActionName']}.",
                                     "evidence": key, "confidence": "medium",
                                     "recommendation": "Review the role policy and simulation context; this is not a live authorization test."})
    vpc = instance.get("VpcId")
    if vpc:
        def read_endpoints():
            result, token = [], None
            while True:
                args = {"Filters": [{"Name": "vpc-id", "Values": [vpc]},
                    {"Name": "service-name", "Values": [
                        f"com.amazonaws.{region}.{s}" for s in ("ssm", "ssmmessages", "ec2messages")]}]}
                if token:
                    args["NextToken"] = token
                page = ec2.describe_vpc_endpoints(**args)
                result.extend(page["VpcEndpoints"])
                token = page.get("NextToken")
                if not token:
                    return result

        endpoints = check("ssm_endpoints", read_endpoints)
        groups = sorted({g["GroupId"] for endpoint in endpoints or [] for g in endpoint.get("Groups", [])})
        if groups:
            check("ssm_endpoint_security_groups", lambda: ec2.describe_security_groups(GroupIds=groups)["SecurityGroups"])
        for endpoint in endpoints or []:
            if endpoint.get("State") not in {None, "available"} or endpoint.get("PrivateDnsEnabled") is False:
                findings.append({"finding": "An SSM endpoint is unavailable or private DNS is disabled.",
                                 "evidence": "ssm_endpoints", "endpoint_id": endpoint.get("VpcEndpointId"),
                                 "confidence": "medium", "recommendation": "Verify the intended endpoint or public egress path; this configuration alone does not establish the offline cause."})
            if "State" not in endpoint or "PrivateDnsEnabled" not in endpoint:
                gaps.append({"check": "ssm.endpoint_configuration", "status": "unknown",
                             "message": "Endpoint state or private DNS evidence is incomplete."})
    gaps.extend([
        {"check": "ssm.agent", "status": "unknown", "message": "An offline SSM record does not prove the agent is stopped or absent. Requires host telemetry or local agent logs."},
        {"check": "ssm.connectivity", "status": "unknown", "message": "Endpoint inventory is not reachability: verify endpoint SGs/policies, private DNS, egress 443, NAT/IGW path and host DNS. Missing endpoints alone is not a fault when public egress works."},
        {"check": "ssm.effective_authorization", "status": "unknown", "message": "Role simulation may omit organization policies and runtime context; alternative SSM credential modes, metadata access and live agent credentials are not verified."},
    ])
