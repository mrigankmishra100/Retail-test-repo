"""Verify existing Langfuse ingestion using one synthetic application trace.

Creates one observability record; does not call an LLM or the retail database.
Private project credentials are read in memory and never included in reports.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4


def clean_url(value):
    if not isinstance(value, str) or any(char.isspace() for char in value):
        raise ValueError("Invalid endpoint")
    url = urlsplit(value)
    if (url.scheme not in {"http", "https"} or not url.hostname
            or url.username is not None or url.password is not None or url.query or url.fragment):
        raise ValueError("Invalid endpoint")
    url.port  # Reject malformed ports before making any network call.
    return value.rstrip("/")


def safe_id(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value))


def verify(values, wait_seconds=30, *, client_factory=None, tracing_factory=None,
           clock=time.monotonic, sleep=time.sleep):
    report = dict(status="failed", health_ok=False, authenticated=False, trace_observed=False)
    tracing = None
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        if not 0 <= wait_seconds <= 90:
            raise ValueError("Invalid wait")
        base = clean_url(values.get("LANGFUSE_BASE_URL"))
        public, secret = values.get("LANGFUSE_PUBLIC_KEY"), values.get("LANGFUSE_SECRET_KEY")
        if not all(isinstance(key, str) and key.strip() for key in (public, secret)):
            raise ValueError("Missing credentials")
        environment = values.get("LANGFUSE_ENVIRONMENT") or "poc"
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", environment) or environment.startswith("langfuse"):
            raise ValueError("Invalid environment")
        proxy = values.get("LANGFUSE_HTTP_PROXY") or None
        if proxy:
            proxy = clean_url(proxy)
        if client_factory is None:
            from httpx import Client
            client_factory = Client
        if tracing_factory is None:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
            from app.observability.langfuse import LangfuseObservabilityClient
            tracing_factory = LangfuseObservabilityClient
        auth = (public.strip(), secret.strip())
        with client_factory(timeout=10, proxy=proxy, trust_env=False, follow_redirects=False) as client:
            report["reason"] = "health_unavailable"
            response = client.get(base + "/api/public/health", params={"failIfDatabaseUnavailable": "true"})
            report["health_http_status"] = response.status_code
            if response.status_code != 200:
                return report
            report["health_ok"] = True
            report["reason"] = "authentication_failed"
            response = client.get(base + "/api/public/projects", auth=auth)
            report["auth_http_status"] = response.status_code
            if response.status_code != 200:
                return report
            projects = response.json().get("data")
            if not isinstance(projects, list) or not projects:
                return report
            project = projects[0].get("id") if isinstance(projects[0], dict) else None
            report["authenticated"] = True
            report["reason"] = "trace_not_observed"
            name = "v5-integration-check-" + str(uuid4())
            started = datetime.now(timezone.utc)
            # v4 reads observations rather than removed legacy trace endpoints.
            # Exclude inputs/outputs and bound this unique observation query.
            query = {"name": name, "limit": 10, "fields": "core,basic",
                     "fromStartTime": (started - timedelta(minutes=2)).isoformat(),
                     "toStartTime": (started + timedelta(seconds=wait_seconds + 120)).isoformat()}
            tracing = tracing_factory(enabled=True, base_url=base, public_key=auth[0],
                                      secret_key=auth[1], http_proxy=proxy, environment=environment)
            with tracing.observation(name=name, input={"check": "synthetic validation only"},
                                     metadata={"source": "v5-integration-check", "synthetic": True}) as observation:
                observation.update(output={"result": "ok"})
            tracing.flush()
            deadline = clock() + wait_seconds
            while True:
                response = client.get(base + "/api/public/v2/observations", auth=auth, params=query)
                report["read_http_status"] = response.status_code
                if response.status_code != 200:
                    report["reason"] = "trace_query_failed"
                    return report
                records = response.json().get("data", [])
                if not isinstance(records, list):
                    raise ValueError("Invalid trace response")
                for record in records:
                    if isinstance(record, dict) and record.get("name") == name and safe_id(record.get("traceId")):
                        report.update(status="passed", trace_observed=True, trace_id=record["traceId"],
                                      reason="verified", read_api="observations_v2")
                        if safe_id(record.get("id")):
                            report["observation_id"] = record["id"]
                        if safe_id(project):
                            report["trace_url"] = base + "/project/" + project + "/traces/" + record["traceId"]
                        return report
                remaining = deadline - clock()
                if remaining <= 0:
                    return report
                sleep(min(2, remaining))
    except Exception:
        report.setdefault("reason", "configuration_invalid")
        return report
    finally:
        if tracing is not None:
            try:
                tracing.close()
            except Exception:
                pass
        logging.disable(previous_logging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--langfuse-env", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    if not 0 <= args.wait_seconds <= 90:
        parser.error("--wait-seconds must be between 0 and 90")
    try:
        from dotenv import dotenv_values
        with args.langfuse_env.open(encoding="utf-8-sig") as stream:
            values = dotenv_values(stream=stream, interpolate=False)
        report = verify(values, args.wait_seconds)
    except Exception:
        report = dict(status="failed", reason="configuration_unavailable", health_ok=False,
                      authenticated=False, trace_observed=False)
    rendered = json.dumps(report, indent=2)
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as output:
                output.write(rendered + "\n")
        except OSError:
            print(json.dumps({"status": "failed", "reason": "report_output_unavailable"}))
            return 1
    print(rendered)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
