"""AWS-enforced diagnostic session boundary, also used by the example policy."""
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
