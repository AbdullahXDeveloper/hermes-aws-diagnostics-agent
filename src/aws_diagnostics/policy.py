"""AWS-enforced diagnostic session boundary, also used by the example policy."""
import re


def root_opt_in(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        if value.strip().lower() in {"true", "1", "yes"}:
            return True
        if value.strip().lower() in {"false", "0", "no", ""}:
            return False
    raise ValueError("AWS_DIAGNOSTICS_ALLOW_ROOT must be true or false")


def valid_role_arn(value):
    return isinstance(value, str) and bool(re.fullmatch(
        r"arn:aws(?:-us-gov|-cn)?:iam::\d{12}:role/[\w+=,.@/-]+", value))


READ_ACTIONS = [
    "sts:GetCallerIdentity", "ec2:DescribeInstances", "ec2:DescribeInstanceStatus",
    "ec2:DescribeRegions", "ec2:DescribeSecurityGroups", "ec2:DescribeNetworkAcls",
    "ec2:DescribeRouteTables", "ec2:DescribeInternetGateways", "ec2:DescribeVpcEndpoints",
    "ssm:DescribeInstanceInformation", "iam:GetInstanceProfile", "iam:SimulatePrincipalPolicy",
    "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics", "logs:GetLogEvents",
]
SESSION_POLICY = {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": READ_ACTIONS, "Resource": "*"},
    {"Effect": "Deny", "NotAction": READ_ACTIONS, "Resource": "*"},
]}
