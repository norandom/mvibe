"""Read-only, offline validation of deliberately sanitized operator observations.

This module imports no application code and provides no live execution option.
There are no free-text result/configuration fields. Validation cannot establish
that an observation occurred, or detect data deliberately encoded as a number.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sys

OPERATIONS = ("browser_oidc", "credential_reuse", "tenant_selection", "models", "chat",
              "streaming", "tool_exchange", "embeddings", "refresh")
CONSUMERS = ("proxy", "direct_provider")
REASONS = {"UNVERIFIED": {"not_exercised"}, "PASS": {"observed"},
           "ENTITLEMENT_LIMITATION": {"model_unavailable", "capability_unavailable"},
           "FAIL": {"authentication_failed", "service_error", "client_error", "unknown_failure"}}
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,6}")
MAX_BYTES = 65536


def require(condition):
    if not condition:
        # Never interpolate rejected input, paths, JSON keys or parser errors.
        raise ValueError("Invalid live evidence report")


def shape(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def choice(value, options):
    require(type(value) is str and value in options)


def timestamp(value):
    require(type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value))
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise ValueError("Invalid live evidence report") from None


def validate_report(report):
    """Return a valid report, or raise ValueError without disclosing its contents."""
    shape(report, ("schema_version", "offline", "local_startup", "consumers", "operations"))
    require(type(report["schema_version"]) is int and report["schema_version"] == 1)
    for section in ("offline", "local_startup"):
        record = report[section]
        shape(record, ("status", "observed_at"))
        choice(record["status"], ("PASS", "FAIL", "UNVERIFIED"))
        if record["status"] == "UNVERIFIED":
            require(record["observed_at"] is None)
        else:
            timestamp(record["observed_at"])
    shape(report["consumers"], CONSUMERS)
    for consumer in CONSUMERS:
        metadata = report["consumers"][consumer]
        shape(metadata, ("versions", "configuration"))
        versions = metadata["versions"]
        if versions is not None:
            shape(versions, ("client", "provider", "python", "host", "bun"))
            for value in versions.values():
                require(value is None or (type(value) is str and VERSION.fullmatch(value)))
            require(all(versions[name] is not None for name in ("client", "provider", "python")))
            if consumer == "direct_provider":
                require(versions["host"] is not None and versions["bun"] is not None)
        config = metadata["configuration"]
        if config is not None:
            shape(config, ("issuer", "client", "audience", "tenant", "callback_port", "transport", "model_selection"))
            for field in ("issuer", "client", "audience"):
                choice(config[field], ("default", "custom"))
            choice(config["tenant"], ("automatic", "organization_override", "workspace_override", "both_overrides"))
            choice(config["transport"], ("default", "custom_ca", "proxy", "proxy_custom_ca"))
            choice(config["model_selection"], ("account_catalog", "none"))
            require(type(config["callback_port"]) is int and 1 <= config["callback_port"] <= 65535)
    require(type(report["operations"]) is list and len(report["operations"]) == len(CONSUMERS) * len(OPERATIONS))
    seen = set()
    for row in report["operations"]:
        shape(row, ("consumer", "operation", "status", "reason", "observed_at"))
        choice(row["consumer"], CONSUMERS)
        choice(row["operation"], OPERATIONS)
        pair = (row["consumer"], row["operation"])
        require(pair not in seen)
        seen.add(pair)
        choice(row["status"], REASONS)
        choice(row["reason"], REASONS[row["status"]])
        if row["status"] == "UNVERIFIED":
            require(row["observed_at"] is None)
        else:
            timestamp(row["observed_at"])
            metadata = report["consumers"][row["consumer"]]
            require(metadata["versions"] is not None and metadata["configuration"] is not None)
    return report


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0].startswith("-"):
        print("Usage: live_evidence.py REPORT.json (read-only; no live actions)", file=sys.stderr)
        return 1
    try:
        # Bound the read itself, including pipes and files changed during reading.
        with Path(args[0]).open("rb") as source:
            raw = source.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES)
        report = json.loads(raw, object_pairs_hook=unique_object)
        validate_report(report)
    except (OSError, ValueError, TypeError, RecursionError):
        print("Invalid or unreadable live evidence report; rejected contents omitted.", file=sys.stderr)
        return 1
    print("Valid sanitized structure; not proof of live execution or compatibility.")
    for consumer in CONSUMERS:
        counts = {status: sum(row["consumer"] == consumer and row["status"] == status
                              for row in report["operations"]) for status in REASONS}
        print(consumer + ": " + ", ".join(f"{status}={count}" for status, count in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
