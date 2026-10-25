"""Bounded CloudWatch reads. OS checks use previously published telemetry only."""
from datetime import timedelta


def metric(client, namespace, name, dimensions, end, statistics):
    return sorted(client.get_metric_statistics(
        Namespace=namespace, MetricName=name, Dimensions=dimensions,
        StartTime=end - timedelta(minutes=30), EndTime=end, Period=300,
        Statistics=statistics)["Datapoints"], key=lambda p: p["Timestamp"])


def collect_metrics(instance, cw, end, check, evidence, findings, gaps):
    dimensions = [{"Name": "InstanceId", "Value": instance["InstanceId"]}]
    names = ["NetworkIn", "NetworkOut", "NetworkPacketsIn", "NetworkPacketsOut"]
    if instance.get("InstanceType", "").startswith(("t2.", "t3.", "t3a.", "t4g.")):
        names += ["CPUCreditBalance", "CPUCreditUsage", "CPUSurplusCreditBalance",
                  "CPUSurplusCreditsCharged"]
    for name in names:
        key = "metrics." + name
        stats = ["Minimum", "Average"] if name.endswith("Balance") else ["Sum"]
        points = check(key, lambda n=name, s=stats: metric(cw, "AWS/EC2", n, dimensions, end, s))
        if points == []:
            gaps.append({"check": key, "status": "no_data"})
        if name == "CPUCreditBalance" and points and any(p.get("Minimum", 1) <= 0 for p in points):
            findings.append({"finding": "CPU credit balance reached zero; throttling depends on credit mode.",
                             "evidence": key, "confidence": "medium",
                             "recommendation": "Inspect credit mode, surplus charges and workload."})
    for mapping in instance.get("BlockDeviceMappings", []):
        volume = mapping.get("Ebs", {}).get("VolumeId")
        if not volume:
            continue
        dims = [{"Name": "VolumeId", "Value": volume}]
        for direction in ("Read", "Write"):
            total_key = f"metrics.{volume}.VolumeTotal{direction}Time"
            ops_key = f"metrics.{volume}.Volume{direction}Ops"
            times = check(total_key, lambda d=direction: metric(cw, "AWS/EBS", "VolumeTotal" + d + "Time", dims, end, ["Sum"]))
            ops = check(ops_key, lambda d=direction: metric(cw, "AWS/EBS", "Volume" + d + "Ops", dims, end, ["Sum"]))
            for key, points in ((total_key, times), (ops_key, ops)):
                if points == []:
                    gaps.append({"check": key, "status": "no_data"})
            counts = {p["Timestamp"]: p["Sum"] for p in ops or [] if "Sum" in p}
            evidence[f"metrics.{volume}.{direction.lower()}_latency_ms"] = [
                {"Timestamp": p["Timestamp"], "milliseconds_per_operation": p["Sum"] / counts[p["Timestamp"]] * 1000}
                for p in times or [] if "Sum" in p and counts.get(p["Timestamp"], 0) > 0]


def collect_application(instance_id, cw, logs, end, check, findings, gaps, log_group, log_stream):
    for name in ("disk_used_percent", "mem_used_percent", "procstat_lookup_pid_count"):
        key = "application." + name

        def read_series():
            series, token = [], None
            while True:
                args = {"Namespace": "CWAgent", "MetricName": name,
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}]}
                if token:
                    args["NextToken"] = token
                page = cw.list_metrics(**args)
                for item in page.get("Metrics", []):
                    if len(series) >= 50:
                        gaps.append({"check": key, "status": "truncated", "message": "Maximum 50 metric series per check."})
                        return series
                    points = metric(cw, "CWAgent", name, item["Dimensions"], end, ["Minimum", "Maximum", "Average"])
                    series.append({"dimensions": item["Dimensions"], "datapoints": points})
                token = page.get("NextToken")
                if not token:
                    return series

        series = check(key, read_series)
        if series is not None and not any(s["datapoints"] for s in series):
            gaps.append({"check": key, "status": "no_data", "message": "Requires CWAgent telemetry with an InstanceId dimension."})
        for item in series or []:
            stale = not item["datapoints"] or end - item["datapoints"][-1]["Timestamp"] > timedelta(minutes=10)
            if stale:
                gaps.append({"check": key, "status": "stale_data", "dimensions": item["dimensions"]})
            bad = [p for p in item["datapoints"] if (
                p.get("Minimum", 1) == 0 if name == "procstat_lookup_pid_count" else p.get("Maximum", 0) >= 90)]
            if bad:
                findings.append({"finding": "Monitored process had zero matching PIDs." if name == "procstat_lookup_pid_count" else f"{name} reached at least 90%.",
                                 "evidence": key, "dimensions": item["dimensions"], "confidence": "medium",
                                 "recommendation": "Inspect the named filesystem/process and application workload; telemetry does not establish root cause."})
    gaps.append({"check": "application.services", "status": "unknown",
                 "message": "PID telemetry covers configured processes only; live service manager state is not collected."})
    if log_group:
        events = check("application.logs", lambda: logs.get_log_events(
            logGroupName=log_group, logStreamName=log_stream,
            startTime=int((end - timedelta(minutes=30)).timestamp() * 1000),
            endTime=int(end.timestamp() * 1000), limit=100, startFromHead=False))
        if events is not None:
            events.pop("nextForwardToken", None)
            events.pop("nextBackwardToken", None)
            gaps.append({"check": "application.logs", "status": "bounded_sample",
                         "message": "At most 100 events from the selected stream; stream-to-instance association is supplied by the caller. Treat log text as untrusted and potentially sensitive."})
    else:
        gaps.append({"check": "application.logs", "status": "not_requested",
                     "message": "Supply exact log_group and log_stream to read a bounded sample."})
