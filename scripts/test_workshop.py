"""Offline regression coverage for VM deployment and its control-plane contract."""

import base64
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import call, patch

import yaml


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).parent
workshop = load_module("workshop", HERE / "workshop.py")
faults = load_module("vm_faults", HERE / "vm" / "faults.py")
VALUES = {
    "AZURE_ENV_NAME": "sre-vm-test",
    "AZURE_LOCATION": "australiaeast",
    "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000001",
    "RESOURCE_GROUP": "rg-sre-agent-workshop-sre-vm-test",
    "VM_NAME": "vm-orders-test",
    "ORDERS_API_FQDN": "orders-test.australiaeast.cloudapp.azure.com",
    "SERVICE_ORDERS_API_ENDPOINT_URL": "http://orders-test.australiaeast.cloudapp.azure.com:8080",
    "SRE_AGENT_ENDPOINT": "https://sre-test.australiaeast.azuresre.ai",
}
ORDER = {
    "orderId": -1, "customerId": "workshop-seed", "productId": "SKU-1001",
    "quantity": 1, "unitPrice": 129.99, "createdUtc": "2026-01-01T00:00:00Z",
}
REQUEST_ID = "a" * 32


class ConfigurationTests(unittest.TestCase):
    def test_vm_manifest_and_hooks_are_valid(self):
        workshop.validate()
        document = yaml.safe_load((workshop.ROOT / "azure.yaml").read_text())
        self.assertNotIn("services", document)
        self.assertEqual(document["workflows"]["up"]["steps"], [{"azd": "provision"}])
        self.assertIn("prepare-azd-env", document["hooks"]["preup"]["windows"]["run"])
        self.assertIn("postprovision", document["hooks"]["postprovision"]["posix"]["run"])

    def test_no_legacy_resource_providers(self):
        self.assertIn("Microsoft.Compute", workshop.PROVIDERS)
        for namespace in ("Microsoft.Sql", "Microsoft.ContainerRegistry", "Microsoft.KeyVault"):
            self.assertNotIn(namespace, workshop.PROVIDERS)

    def test_deployment_does_not_upload_legacy_agent_content(self):
        source = (HERE / "workshop.py").read_text()
        self.assertNotIn("configure_agent", source)
        self.assertNotIn("knowledge.yaml", source)
        self.assertNotIn("incident-filters.yaml", source)

    def test_service_fails_closed_without_disk_and_runs_unprivileged(self):
        service = (HERE / "vm" / "orders-api.service").read_text()
        for setting in (
            "RequiresMountsFor=/var/lib/orders",
            "ExecStartPre=/usr/bin/mountpoint --quiet /var/lib/orders",
            "Restart=always", "User=orders", "Group=orders",
            "WantedBy=multi-user.target", "ProtectSystem=strict", "NoNewPrivileges=true",
        ):
            self.assertIn(setting, service)
        self.assertNotIn("User=root", service)

    def test_install_uses_lun_and_uuid_and_preserves_existing_filesystem(self):
        source = (HERE / "vm" / "install.sh").read_text()
        for expected in (
            "/dev/disk/azure/scsi1/lun0", "UUID=${disk_uuid}",
            "Refusing to overwrite a non-ext4 data disk",
            "Refusing to format a disk with partitions",
            "Refusing to format a disk with existing signatures",
            "Refusing to adopt an unrelated ext4 data disk",
            '[[ ! -f "${release}/OrdersApi.dll" ]]',
            "export HOME=/root DOTNET_CLI_HOME=/root",
            "--bootstrap",
        ):
            self.assertIn(expected, source)
        self.assertNotIn("/dev/sda", source)

    def test_payload_is_deterministic_and_excludes_build_outputs_and_secrets(self):
        first, digest = workshop.bundle()
        second, other_digest = workshop.bundle()
        self.assertEqual((first, digest), (second, other_digest))
        self.assertLess(len(first), 50000)
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(base64.b64decode(first)))) as archive:
            names = archive.getnames()
            self.assertIn("app/OrdersApi.csproj", names)
            self.assertIn("vm/orders-api.service", names)
            self.assertIn("vm/faults.py", names)
            self.assertFalse(any(part in path for path in names for part in (
                "bin/", "obj/", ".env", ".azure", "Catalog", "agent/",
            )))
            self.assertTrue(all(not member.name.startswith("/") and member.mtime == 0
                                for member in archive.getmembers()))

    def test_new_resource_group_clears_only_known_runtime_evidence(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                workshop, "ROOT", Path(temporary)):
            directory = workshop.state_directory(VALUES)
            for filename in workshop.FRESH_ENVIRONMENT_EVIDENCE:
                (directory / filename).write_text("stale")
            deployment = directory / "deployment.json"
            unrelated = directory / "manual-notes.json"
            deployment.write_text("current")
            unrelated.write_text("keep")

            workshop.clear_fresh_environment_evidence(VALUES)

            self.assertTrue(all(
                not (directory / filename).exists()
                for filename in workshop.FRESH_ENVIRONMENT_EVIDENCE
            ))
            self.assertEqual(deployment.read_text(), "current")
            self.assertEqual(unrelated.read_text(), "keep")


class PreflightTests(unittest.TestCase):
    def test_new_resource_group_requires_no_inventory_calls(self):
        with patch.object(workshop, "az", return_value=False) as az:
            workshop.check_resource_group(VALUES)
        az.assert_called_once_with("group", "exists", "--name", VALUES["RESOURCE_GROUP"],
                                   "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"])

    def test_existing_legacy_group_is_not_migrated_or_deleted(self):
        with patch.object(workshop, "az", side_effect=[True, {"azd-env-name": "sre-vm-test"}]) as az:
            with self.assertRaisesRegex(workshop.DeploymentError, "new azd environment"):
                workshop.check_resource_group(VALUES)
        self.assertEqual(az.call_count, 2)
        self.assertNotIn("delete", str(az.call_args_list))

    def test_existing_vm_group_can_be_redeployed(self):
        with patch.object(workshop, "az", side_effect=[True, {"workshop-architecture": "single-vm"}]):
            workshop.check_resource_group(VALUES)

    def test_benchmark_rejects_even_an_existing_vm_group(self):
        with patch.object(workshop, "az", return_value=True):
            with self.assertRaisesRegex(workshop.DeploymentError, "already exists"):
                workshop.check_resource_group(VALUES, fresh=True)

    def test_wrong_benchmark_region_stops_before_starting_azd(self):
        with patch.object(workshop, "environment", return_value={**VALUES, "AZURE_LOCATION": "eastus2"}), \
                patch.object(workshop, "check_resource_group"), patch.object(workshop.subprocess, "Popen") as run:
            with self.assertRaisesRegex(workshop.DeploymentError, "australiaeast"):
                workshop.benchmark()
        run.assert_not_called()

    def test_ssh_key_is_stable_on_repeated_deployment(self):
        values = {**VALUES, "VM_SSH_PUBLIC_KEY": "ssh-rsa dGVzdA== workshop"}
        with patch.object(workshop, "cli") as cli:
            workshop.ensure_ssh_public_key(values)
        cli.assert_not_called()

    def test_invalid_ssh_key_fails_before_provision(self):
        with patch.object(workshop, "cli") as cli:
            with self.assertRaises(workshop.DeploymentError):
                workshop.ensure_ssh_public_key({**VALUES, "VM_SSH_PUBLIC_KEY": "not a public key\n"})
        cli.assert_not_called()

    def test_provider_registration_is_case_insensitive_and_minimal(self):
        registered = [{"namespace": item.lower(), "state": "Registered"} for item in workshop.PROVIDERS]
        initial = [dict(row, state="NotRegistered") if row["namespace"] == "microsoft.compute"
                   else row for row in registered]
        with patch.object(workshop, "az", side_effect=[initial, None, registered]) as az, \
                patch.object(workshop.time, "sleep"):
            workshop.register_providers()
        self.assertIn(call("provider", "register", "--namespace", "Microsoft.Compute"), az.call_args_list)
        self.assertEqual(sum(args.args[1] == "register" for args in az.call_args_list), 1)

    def test_provider_permanent_error_is_not_retried(self):
        with patch.object(workshop, "az", side_effect=workshop.DeploymentError("AuthorizationFailed")) as az:
            with self.assertRaisesRegex(workshop.DeploymentError, "AuthorizationFailed"):
                workshop.register_providers()
        self.assertEqual(az.call_count, 1)

    def test_subscription_selection_is_explicit(self):
        with patch.object(workshop, "cli", return_value=json.dumps(VALUES)), patch.object(workshop, "az") as az:
            self.assertEqual(workshop.environment(), VALUES)
        az.assert_called_once_with("account", "set", "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"])

    def test_exports_allowlist_and_shell_quoting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {"RESOURCE_GROUP": "rg-'$(echo unsafe)", "FAULT_TOKEN": "do-not-export",
                      "APPLICATIONINSIGHTS_CONNECTION_STRING": "do-not-export",
                      "VM_SSH_PUBLIC_KEY": "do-not-export", "UNEXPECTED_SECRET": "do-not-export"}
            with patch.object(workshop, "ROOT", root):
                workshop.export_values(values)
            bash = (root / ".workshop" / "workshop.env").read_text()
            powershell = (root / ".workshop" / "workshop.ps1").read_text()
            self.assertNotIn("do-not-export", bash + powershell)
            self.assertIn("$env:RESOURCE_GROUP = 'rg-''$(echo unsafe)'", powershell)
            self.assertIn("export RESOURCE_GROUP=", bash)

    def test_stage_reports_permanent_failure_with_stage_name(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(workshop, "ROOT", Path(temporary)):
            with self.assertRaisesRegex(workshop.DeploymentError, r"\[sqlite\] invalid schema"):
                with workshop.stage(VALUES, "sqlite"):
                    raise workshop.DeploymentError("invalid schema")
            report = json.loads((workshop.state_directory(VALUES) / "deployment.json").read_text())
            self.assertEqual(report["stages"][0]["status"], "failed")


class CapacityTests(unittest.TestCase):
    def sku(self, architecture="x64", restrictions=None):
        return {
            "name": "Standard_D2as_v5", "family": "standardDASv5Family",
            "restrictions": restrictions or [],
            "capabilities": [
                {"name": "CpuArchitectureType", "value": architecture},
                {"name": "HyperVGenerations", "value": "V1,V2"},
                {"name": "vCPUs", "value": "2"},
                {"name": "MemoryGB", "value": "8"},
            ],
        }

    def quotas(self, used=0, limit=4):
        return [{"name": {"value": name}, "currentValue": used, "limit": limit}
                for name in ("cores", "standardDASv5Family")]

    def test_selected_size_needs_both_regional_and_family_quota(self):
        with patch.object(workshop, "az", side_effect=[[self.sku()], self.quotas()]) as az:
            workshop.check_vm_capacity(VALUES)
        self.assertEqual(az.call_count, 2)
        self.assertTrue(all("--subscription" in args.args for args in az.call_args_list))

    def test_offer_restriction_fails_before_quota_or_provisioning(self):
        restriction = {"type": "Location", "reasonCode": "NotAvailableForSubscription"}
        with patch.object(workshop, "az", return_value=[self.sku(restrictions=[restriction])]) as az:
            with self.assertRaisesRegex(workshop.DeploymentError, "No subscription upgrade"):
                workshop.check_vm_capacity(VALUES)
        self.assertEqual(az.call_count, 1)

    def test_zone_only_restriction_does_not_block_non_zonal_vm(self):
        restriction = {"type": "Zone", "reasonCode": "NotAvailableForSubscription"}
        with patch.object(workshop, "az", side_effect=[
            [self.sku(restrictions=[restriction])], self.quotas(),
        ]):
            workshop.check_vm_capacity(VALUES)

    def test_arm_free_sku_is_not_silently_used_with_x64_image(self):
        with patch.object(workshop, "az", return_value=[self.sku(architecture="Arm64")]):
            with self.assertRaisesRegex(workshop.DeploymentError, "not x64"):
                workshop.check_vm_capacity(VALUES)

    def test_no_free_account_quota_increase_is_attempted(self):
        for quotas in (
            self.quotas(used=3),
            [self.quotas()[0], self.quotas(limit=0)[1]],
        ):
            with self.subTest(quotas=quotas), \
                    patch.object(workshop, "az", side_effect=[[self.sku()], quotas]) as az:
                with self.assertRaisesRegex(workshop.DeploymentError, "cannot request quota"):
                    workshop.check_vm_capacity(VALUES)
                self.assertEqual(az.call_count, 2)

    def test_missing_quota_is_not_assumed_unlimited(self):
        with patch.object(workshop, "az", side_effect=[[self.sku()], []]):
            with self.assertRaisesRegex(workshop.DeploymentError, "did not return"):
                workshop.check_vm_capacity(VALUES)

    def test_azure_cli_string_quota_values_are_parsed(self):
        quotas = [{**row, "currentValue": "0", "limit": "4"} for row in self.quotas()]
        with patch.object(workshop, "az", side_effect=[[self.sku()], quotas]):
            workshop.check_vm_capacity(VALUES)

    def test_invalid_quota_values_fail_explicitly(self):
        for value in ("unlimited", -1, True, 1.5, "4.5"):
            quotas = self.quotas()
            quotas[0]["limit"] = value
            with self.subTest(value=value), patch.object(workshop, "az", side_effect=[[self.sku()], quotas]):
                with self.assertRaisesRegex(workshop.DeploymentError, "invalid limit"):
                    workshop.check_vm_capacity(VALUES)

    def test_repeat_deploy_does_not_require_unused_quota_for_existing_vm(self):
        values = {**VALUES, "VM_RESOURCE_ID": "/vm"}
        vm = {"id": "/vm", "hardwareProfile": {"vmSize": "Standard_D2as_v5"}}
        with patch.object(workshop, "az", side_effect=[True, [vm]]) as az:
            workshop.check_vm_capacity(values)
        self.assertEqual(az.call_count, 2)
        self.assertNotIn("list-skus", str(az.call_args_list))
        self.assertNotIn("list-usage", str(az.call_args_list))

    def test_down_then_up_rechecks_quota_instead_of_requiring_deleted_vm(self):
        values = {**VALUES, "VM_RESOURCE_ID": "/vm"}
        with patch.object(workshop, "az", side_effect=[False, [self.sku()], self.quotas()]) as az:
            workshop.check_vm_capacity(values)
        self.assertIn("list-usage", str(az.call_args_list))


class RunCommandTests(unittest.TestCase):
    def response(self, result=None, **entry):
        result = {"ok": True} if result is None else result
        return {"value": [{
            "code": "ProvisioningState/succeeded", "level": "Info",
            "message": f"Enable succeeded:\n[stdout]\nWORKSHOP_RESULT:{REQUEST_ID}:{json.dumps(result)}\n[stderr]\n",
            **entry,
        }]}

    def test_correlated_verified_success_is_required(self):
        self.assertEqual(workshop.run_result(self.response(), REQUEST_ID), {"ok": True})
        for response in (
            {"value": []}, {"value": [{"code": "ProvisioningState/succeeded", "message": "exit 1"}]},
            self.response({"ok": False}), self.response(code="ProvisioningState/failed"),
            self.response(level="Error"), self.response(message="WORKSHOP_RESULT:other:{\"ok\":true}"),
            self.response(message=f"WORKSHOP_RESULT:{REQUEST_ID}:not-json"),
        ):
            with self.subTest(response=response), self.assertRaises(workshop.DeploymentError):
                workshop.run_result(response, REQUEST_ID)

    def test_duplicate_result_is_not_accepted(self):
        response = self.response()
        response["value"] *= 2
        with self.assertRaisesRegex(workshop.DeploymentError, "correlated"):
            workshop.run_result(response, REQUEST_ID)

    def test_native_azure_diagnostic_is_preserved(self):
        completed = subprocess.CompletedProcess([], 1, "", "ERROR: RequestDisallowedByPolicy: allowedLocations")
        with patch.object(workshop.shutil, "which", return_value="az"), \
                patch.object(workshop.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(workshop.DeploymentError, "RequestDisallowedByPolicy"):
                workshop.az("vm", "show")

    def test_credential_command_errors_are_redacted(self):
        completed = subprocess.CompletedProcess([], 1, "private-token", "private-token")
        with patch.object(workshop.shutil, "which", return_value="az"), \
                patch.object(workshop.subprocess, "run", return_value=completed):
            with self.assertRaises(workshop.DeploymentError) as error:
                workshop.az("account", "get-access-token", sensitive=True)
        self.assertNotIn("private-token", str(error.exception))

    def test_run_command_is_not_retried_after_ambiguous_failure(self):
        with patch.object(workshop, "az", side_effect=workshop.DeploymentError("timeout")) as az:
            with self.assertRaisesRegex(workshop.DeploymentError, "timeout"):
                workshop.run_vm(VALUES, "echo failed")
        self.assertEqual(az.call_count, 1)

    def test_run_command_uses_a_file_and_bash_without_public_network_auth(self):
        def execute(*args, **kwargs):
            self.assertEqual(args[:3], ("vm", "run-command", "invoke"))
            path = Path(args[args.index("--scripts") + 1][1:])
            content = path.read_text()
            self.assertIn("exec /bin/bash <<", content)
            self.assertIn("set -euo pipefail", content)
            self.assertNotIn("__WORKSHOP_REQUEST_ID__", content)
            request_id = content.split("echo ", 1)[1].splitlines()[0]
            return {"value": [{"message": f'WORKSHOP_RESULT:{request_id}:{{"ok":true}}'}]}
        with patch.object(workshop, "az", side_effect=execute):
            self.assertTrue(workshop.run_vm(VALUES, "echo __WORKSHOP_REQUEST_ID__")["ok"])

    def test_payload_limit_fails_before_azure_call(self):
        with patch.object(workshop, "az") as az:
            with self.assertRaisesRegex(workshop.DeploymentError, "64 KiB"):
                workshop.run_vm(VALUES, "a" * 65536)
        az.assert_not_called()


class DeploymentSequenceTests(unittest.TestCase):
    def test_postprovision_deploys_then_smokes_before_exporting_success(self):
        events = []
        values = {
            **VALUES, "VM_RESOURCE_ID": "/vm", "DATA_DISK_RESOURCE_ID": "/disk",
            "LOG_ANALYTICS_ID": "/workspace", "SRE_AGENT_RESOURCE_ID": "/agent",
        }
        with tempfile.TemporaryDirectory() as temporary, patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "environment", return_value=values), \
                patch.object(workshop, "deploy_vm", side_effect=lambda _: events.append("configure")), \
                patch.object(workshop, "ensure_sre_response_plan",
                             side_effect=lambda _: events.append("agent")), \
                patch.object(workshop, "smoke", side_effect=lambda _: events.append("smoke")), \
                patch.object(workshop, "export_values", side_effect=lambda _: events.append("outputs")):
            workshop.postprovision()
            report = json.loads((workshop.state_directory(values) / "deployment.json").read_text())
        self.assertEqual(events, ["configure", "agent", "smoke", "outputs"])
        self.assertEqual(
            [item["name"] for item in report["stages"]],
            ["vm-configuration", "sre-agent-configuration", "public-smoke"],
        )

    def test_vm_configuration_failure_prevents_smoke_and_success_output(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "environment", return_value=VALUES), \
                patch.object(workshop, "deploy_vm", side_effect=workshop.DeploymentError("mount failed")), \
                patch.object(workshop, "ensure_sre_response_plan") as agent, \
                patch.object(workshop, "smoke") as smoke, patch.object(workshop, "export_values") as export:
            with self.assertRaisesRegex(workshop.DeploymentError, r"\[vm-configuration\] mount failed"):
                workshop.postprovision()
        agent.assert_not_called()
        smoke.assert_not_called()
        export.assert_not_called()

    def test_smoke_failure_is_not_replaced_by_success_exports(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "environment", return_value=VALUES), \
                patch.object(workshop, "deploy_vm", return_value={"ok": True}), \
                patch.object(workshop, "ensure_sre_response_plan", return_value={"ok": True}), \
                patch.object(workshop, "smoke",
                             side_effect=workshop.DeploymentError("public endpoint failed")), \
                patch.object(workshop, "export_values") as export:
            with self.assertRaisesRegex(workshop.DeploymentError, r"\[public-smoke\]"):
                workshop.postprovision()
        export.assert_not_called()


class SreAgentTests(unittest.TestCase):
    def plan(self):
        return {
            "name": workshop.SRE_RESPONSE_PLAN_NAME,
            "properties": {
                "incidentPlatform": "AzMonitor",
                "isEnabled": True,
                "priorities": ["Sev1", "Sev2"],
                "handlingAgent": "meta_agent",
                "agentMode": "Review",
            },
        }

    def test_endpoint_must_be_the_sre_agent_https_host(self):
        self.assertEqual(
            workshop.sre_agent_url(VALUES),
            VALUES["SRE_AGENT_ENDPOINT"],
        )
        for endpoint in (
            "http://sre-test.australiaeast.azuresre.ai",
            "https://example.com",
            "https://user@sre-test.australiaeast.azuresre.ai",
            "https://sre-test.australiaeast.azuresre.ai/other",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(workshop.DeploymentError):
                workshop.sre_agent_url({**VALUES, "SRE_AGENT_ENDPOINT": endpoint})

    def test_response_plan_is_idempotently_written_and_verified(self):
        calls = []

        def request(endpoint, token, path, method="GET", body=None):
            calls.append((endpoint, token, path, method, body))
            return None if method == "PUT" else {"value": [self.plan()]}

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "sre_agent_token", return_value="secret-token"), \
                patch.object(workshop, "sre_agent_request", side_effect=request):
            result = workshop.ensure_sre_response_plan(VALUES, attempts=1)
            evidence = json.loads(
                (workshop.state_directory(VALUES) / "sre-agent-configuration.json").read_text()
            )

        self.assertTrue(result["ok"])
        self.assertEqual(evidence, result)
        self.assertEqual(calls[0][3], "PUT")
        self.assertEqual(calls[0][4]["properties"]["priorities"], ["Sev1", "Sev2"])
        self.assertEqual(calls[0][4]["properties"]["agentMode"], "Review")
        self.assertEqual(calls[0][4]["properties"]["handlingAgent"], "meta_agent")
        self.assertEqual(calls[0][4]["properties"]["titleContainsAll"], [])
        self.assertEqual(calls[0][4]["properties"]["maxAutomatedInvestigationAttempts"], 3)
        self.assertTrue(calls[0][4]["properties"]["mergeEnabled"])
        self.assertEqual(calls[1][3], "GET")

    def test_transient_response_plan_failure_is_retried(self):
        transient = workshop.SreAgentRequestError(
            "not ready", status=403, retryable=True,
        )
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "sre_agent_token", return_value="token"), \
                patch.object(workshop, "sre_agent_request", side_effect=[
                    transient, None, {"value": [self.plan()]},
                ]) as request, patch.object(workshop.time, "sleep") as sleep:
            workshop.ensure_sre_response_plan(VALUES, attempts=2, retry_seconds=1)
        self.assertEqual(request.call_count, 3)
        sleep.assert_called_once_with(1)

    def test_permanent_response_plan_failure_is_not_retried(self):
        permanent = workshop.SreAgentRequestError(
            "bad request", status=400, retryable=False,
        )
        with patch.object(workshop, "sre_agent_token", return_value="token"), \
                patch.object(workshop, "sre_agent_request", side_effect=permanent) as request, \
                patch.object(workshop.time, "sleep") as sleep:
            with self.assertRaisesRegex(workshop.DeploymentError, "bad request"):
                workshop.ensure_sre_response_plan(VALUES)
        request.assert_called_once()
        sleep.assert_not_called()


class RestartTests(unittest.TestCase):
    def inspection(self, boot_id, disk_uuid="current-disk"):
        return {
            "ok": True,
            "bootId": boot_id,
            "storage": {"uuid": disk_uuid},
        }

    def test_legacy_witness_is_replaced_and_bound_to_current_disk(self):
        order = {
            **ORDER,
            "orderId": 1,
            "customerId": "vm-persistence-test",
        }
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "await_ready",
                             return_value=VALUES["SERVICE_ORDERS_API_ENDPOINT_URL"]), \
                patch.object(workshop, "inspect_vm", side_effect=[
                    self.inspection("before"),
                    self.inspection("after"),
                ]), \
                patch.object(workshop, "request_json", side_effect=[
                    (201, {"orderId": 1}),
                    (200, order),
                    (200, order),
                ]), \
                patch.object(workshop, "az") as az, \
                patch.object(workshop, "smoke"):
            witness_path = workshop.state_directory(VALUES) / "persistence-witness.json"
            workshop.write_json(witness_path, {"orderId": 99, "order": order})

            result = workshop.verify_restart(VALUES)
            witness = json.loads(witness_path.read_text())

        self.assertTrue(result["ok"])
        self.assertEqual(witness["orderId"], 1)
        self.assertEqual(witness["diskUuid"], "current-disk")
        self.assertEqual(az.call_args.args[:2], ("vm", "restart"))

    def test_missing_order_on_same_disk_is_a_persistence_failure(self):
        witness = {
            "orderId": 1,
            "order": {**ORDER, "orderId": 1},
            "diskUuid": "current-disk",
        }
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "await_ready",
                             return_value=VALUES["SERVICE_ORDERS_API_ENDPOINT_URL"]), \
                patch.object(workshop, "inspect_vm",
                             return_value=self.inspection("before")), \
                patch.object(workshop, "request_json",
                             side_effect=workshop.HttpError(404, "/orders/1")), \
                patch.object(workshop, "az") as az:
            workshop.write_json(
                workshop.state_directory(VALUES) / "persistence-witness.json",
                witness,
            )
            with self.assertRaises(workshop.HttpError):
                workshop.verify_restart(VALUES)
        az.assert_not_called()

    def test_witness_for_another_disk_fails_before_restart(self):
        witness = {
            "orderId": 1,
            "order": {**ORDER, "orderId": 1},
            "diskUuid": "old-disk",
        }
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "await_ready",
                             return_value=VALUES["SERVICE_ORDERS_API_ENDPOINT_URL"]), \
                patch.object(workshop, "inspect_vm",
                             return_value=self.inspection("before")), \
                patch.object(workshop, "request_json") as request, \
                patch.object(workshop, "az") as az:
            workshop.write_json(
                workshop.state_directory(VALUES) / "persistence-witness.json",
                witness,
            )
            with self.assertRaisesRegex(workshop.DeploymentError, "different managed data disk"):
                workshop.verify_restart(VALUES)
        request.assert_not_called()
        az.assert_not_called()


class SmokeTests(unittest.TestCase):
    def test_endpoint_must_match_deployment_dns_and_port(self):
        self.assertEqual(workshop.api_url(VALUES), VALUES["SERVICE_ORDERS_API_ENDPOINT_URL"])
        for url in ("http://127.0.0.1:8080", "https://example.com", "http://user@orders-test.australiaeast.cloudapp.azure.com:8080",
                    VALUES["SERVICE_ORDERS_API_ENDPOINT_URL"] + "/elsewhere",
                    VALUES["SERVICE_ORDERS_API_ENDPOINT_URL"] + "?redirect=1"):
            with self.subTest(url=url), self.assertRaises(workshop.DeploymentError):
                workshop.api_url({**VALUES, "SERVICE_ORDERS_API_ENDPOINT_URL": url})

    def test_meaningful_order_shape_not_just_any_json(self):
        workshop.validate_order(ORDER)
        for key, value in (("orderId", True), ("quantity", 0), ("quantity", True),
                           ("unitPrice", "129.99"), ("unitPrice", float("nan")),
                           ("customerId", ""), ("createdUtc", "yesterday")):
            with self.subTest(key=key, value=value), self.assertRaises(workshop.DeploymentError):
                workshop.validate_order({**ORDER, key: value})

    def test_smoke_is_read_only_and_rejects_http_fault_routes(self):
        results = [(200, {"status": "ready"}), (200, [ORDER] * 5)] + [
            workshop.HttpError(404, path) for path in ("/fault/cpu", "/fault/storage", "/fault/reset")]
        with tempfile.TemporaryDirectory() as temporary, patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "request_json", side_effect=results) as request:
            result = workshop.smoke(VALUES)
        self.assertEqual(result["ordersValidated"], 5)
        self.assertNotIn(call(VALUES["SERVICE_ORDERS_API_ENDPOINT_URL"], "/orders", "POST", {}), request.call_args_list)

    def test_empty_orders_is_not_success(self):
        with patch.object(workshop, "await_ready", return_value="http://example"), \
                patch.object(workshop, "request_json", return_value=(200, [])):
            with self.assertRaisesRegex(workshop.DeploymentError, "five"):
                workshop.smoke(VALUES)

    def test_permanent_http_error_fails_without_sleeping(self):
        with patch.object(workshop, "request_json", side_effect=workshop.HttpError(403, "/health/ready")), \
                patch.object(workshop.time, "sleep") as sleep:
            with self.assertRaises(workshop.HttpError):
                workshop.await_ready(VALUES)
        sleep.assert_not_called()

    def test_transient_readiness_response_is_retried(self):
        with patch.object(workshop, "request_json", side_effect=[
            workshop.HttpError(503, "/health/ready"), (200, {"status": "ready"}),
        ]) as request, patch.object(workshop.time, "sleep"):
            workshop.await_ready(VALUES)
        self.assertEqual(request.call_count, 2)

    def test_malformed_readiness_fails_fast(self):
        with patch.object(workshop, "request_json", return_value=(200, {"vm": "exists"})), \
                patch.object(workshop.time, "sleep") as sleep:
            with self.assertRaisesRegex(workshop.DeploymentError, "Readiness"):
                workshop.await_ready(VALUES)
        sleep.assert_not_called()

    def test_redirect_is_refused(self):
        with self.assertRaises(workshop.DeploymentError):
            workshop.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.com")


class FaultTests(unittest.TestCase):
    def test_fault_limits_are_validated_before_run_command(self):
        for action, args in (
            ("cpu", ["0"]), ("cpu", ["1801"]), ("cpu", ["60", "9"]),
            ("disk", ["98"]), ("disk", ["90", "0"]), ("disk", ["bad"]),
            ("status", ["1"]), ("errors", []), ("reset", ["1"]),
        ):
            with self.subTest(action=action, args=args), patch.object(workshop, "run_vm") as run:
                with self.assertRaises(workshop.DeploymentError):
                    workshop.fault(VALUES, action, args)
                run.assert_not_called()

    def test_faults_use_control_plane_and_bounded_defaults(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "run_vm", return_value={"ok": True}) as run, \
                patch.object(workshop, "request_json") as http:
            workshop.fault(VALUES, "cpu", [])
            self.assertIn("cpu 300 2 --request-id", run.call_args.args[1])
            workshop.fault(VALUES, "disk", [])
            self.assertIn("disk 90 300 --request-id", run.call_args.args[1])
        http.assert_not_called()

    def test_disk_allocation_preserves_recovery_reserve(self):
        total = 8 * 1024**3
        available = 7 * 1024**3
        amount = faults.allocation_size(total, available, 90)
        self.assertAlmostEqual(total - available + amount, total * 0.9, delta=1)
        self.assertGreaterEqual(available - amount, faults.RESERVE_BYTES)
        with self.assertRaisesRegex(RuntimeError, "reserve"):
            faults.allocation_size(1024**3, 300 * 1024**2, 97)
        with self.assertRaisesRegex(RuntimeError, "already"):
            faults.allocation_size(total, 100 * 1024**2, 90)

    def test_cpu_failure_terminates_only_its_owned_processes(self):
        from unittest.mock import Mock
        child = Mock()
        child.poll.return_value = None
        with patch.object(faults.signal, "signal"), \
                patch.object(faults.subprocess, "Popen", side_effect=[child, OSError("spawn failure")]):
            with self.assertRaises(OSError):
                faults.cpu_worker(30, 2)
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=10)

    def test_disk_worker_cleans_ballast_when_allocation_fails(self):
        with patch.object(faults, "usage", return_value=(8 * 1024**3, 7 * 1024**3)), \
                patch.object(faults.signal, "signal"), patch.object(faults.os, "open", return_value=42), \
                patch.object(faults.os, "O_NOFOLLOW", 0, create=True), \
                patch.object(faults.os, "posix_fallocate", side_effect=OSError("no space"), create=True), \
                patch.object(faults.os, "close") as close, patch.object(faults.Path, "unlink") as unlink:
            with self.assertRaises(OSError):
                faults.disk_worker(90, 30)
        close.assert_called_once_with(42)
        unlink.assert_called_once_with(missing_ok=True)

    def test_systemd_faults_have_ttl_and_disk_cleanup(self):
        source = (HERE / "vm" / "faults.py").read_text()
        self.assertEqual(source.count('"--property=RuntimeMaxSec="'), 2)
        self.assertIn("ExecStopPost=/usr/bin/rm -f -- ", source)
        self.assertIn("os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW", source)
        self.assertNotEqual(faults.BALLAST.name, "orders.db")


if __name__ == "__main__":
    unittest.main()
