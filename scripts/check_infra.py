#!/usr/bin/env python3
"""Regression checks against compiled ARM JSON, including every nested Bicep module."""

import json
from pathlib import Path
import sys


def resources(template):
    result = []
    for resource in template.get("resources", []):
        result.append(resource)
        nested = resource.get("properties", {}).get("template")
        if isinstance(nested, dict):
            result.extend(resources(nested))
    return result


def check(template):
    deployed = resources(template)
    by_type = {}
    for resource in deployed:
        by_type.setdefault(resource["type"].lower(), []).append(resource)

    def exactly_one(resource_type):
        matches = by_type.get(resource_type.lower(), [])
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one {resource_type}, found {len(matches)}.")
        return matches[0]

    forbidden = (
        "microsoft.containerregistry/", "microsoft.sql/", "microsoft.keyvault/",
        "microsoft.app/containerapps", "microsoft.app/managedenvironments",
        "microsoft.app/jobs", "microsoft.network/privateendpoints",
        "microsoft.network/privatednszones", "microsoft.resources/deploymentscripts",
    )
    for resource_type in by_type:
        if resource_type.startswith(forbidden):
            raise ValueError(f"Obsolete infrastructure remains: {resource_type}")

    vm = exactly_one("Microsoft.Compute/virtualMachines")
    profile = vm["properties"]
    if profile["osProfile"]["linuxConfiguration"]["disablePasswordAuthentication"] is not True:
        raise ValueError("VM password authentication must remain disabled.")
    disks = profile["storageProfile"]["dataDisks"]
    if len(disks) != 1 or disks[0]["lun"] != 0 or disks[0]["createOption"] != "Attach":
        raise ValueError("SQLite requires one separately provisioned managed disk at LUN 0.")
    if disks[0].get("deleteOption") != "Detach":
        raise ValueError("VM replacement must not implicitly delete the SQLite disk.")
    exactly_one("Microsoft.Compute/disks")
    exactly_one("Microsoft.Network/virtualNetworks")
    exactly_one("Microsoft.Network/networkInterfaces")
    public_ip = exactly_one("Microsoft.Network/publicIPAddresses")
    if public_ip["properties"]["publicIPAllocationMethod"] != "Static":
        raise ValueError("Smoke tests require a static public endpoint.")
    if not public_ip["properties"].get("dnsSettings", {}).get("domainNameLabel"):
        raise ValueError("The VM public IP needs a stable Azure DNS label.")
    nsg = exactly_one("Microsoft.Network/networkSecurityGroups")
    inbound = [rule["properties"] for rule in nsg["properties"]["securityRules"]
               if rule["properties"]["direction"] == "Inbound" and rule["properties"]["access"] == "Allow"]
    if len(inbound) != 1 or inbound[0].get("destinationPortRange") != "8080":
        raise ValueError("The NSG may allow only the public Orders API port 8080.")
    if inbound[0].get("protocol", "").lower() != "tcp":
        raise ValueError("The public API rule must be TCP only.")
    workspace = exactly_one("Microsoft.OperationalInsights/workspaces")
    if workspace["properties"]["retentionInDays"] == 0:
        raise ValueError("Monitoring data needs retained logs.")
    if workspace["properties"].get("workspaceCapping", {}).get("dailyQuotaGb") != 1:
        raise ValueError("The workshop workspace must retain its 1 GB/day ingestion safeguard.")
    exactly_one("Microsoft.Insights/components")
    dcr = exactly_one("Microsoft.Insights/dataCollectionRules")
    counters = [counter for group in dcr["properties"]["dataSources"]["performanceCounters"]
                for counter in group["counterSpecifiers"]]
    if not any("Processor Time" in counter for counter in counters):
        raise ValueError("AMA must collect guest CPU telemetry.")
    if not any("Free Space" in counter for counter in counters):
        raise ValueError("AMA must collect data-disk free space.")
    exactly_one("Microsoft.Insights/dataCollectionRuleAssociations")
    extensions = by_type.get("microsoft.compute/virtualmachines/extensions", [])
    if not any(extension["properties"]["type"] == "AzureMonitorLinuxAgent" for extension in extensions):
        raise ValueError("The Linux Azure Monitor Agent must be installed.")
    agent = exactly_one("Microsoft.App/agents")
    action = agent["properties"]["actionConfiguration"]
    if action.get("accessLevel") != "Low" or action.get("mode") != "Review":
        raise ValueError("The SRE Agent must investigate read-only in Review mode.")
    if agent["properties"].get("monthlyAgentUnitLimit") != 500:
        raise ValueError("The SRE Agent must retain its 500-AAU active usage limit.")
    if agent["properties"].get("defaultModel") != {"provider": "MicrosoftFoundry", "name": "Automatic"}:
        raise ValueError("Use the native SRE Agent model provider, not a separate Marketplace deployment.")
    connectors = by_type.get("microsoft.app/agents/connectors", [])
    kinds = {connector["properties"]["dataConnectorType"] for connector in connectors}
    if kinds != {"AppInsights", "LogAnalytics"} or len(connectors) != 2:
        raise ValueError("SRE Agent requires exactly the Application Insights and Log Analytics connectors.")
    return len(deployed)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 2:
            raise ValueError("Usage: python scripts/check_infra.py <compiled ARM template.json>")
        count = check(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig")))
        print(f"Validated single-VM architecture across {count} compiled ARM resources.")
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Infrastructure regression: {error}", file=sys.stderr)
        sys.exit(1)
