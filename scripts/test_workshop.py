"""Offline regression coverage for VM deployment and its control-plane contract."""

import base64
from contextlib import ExitStack, contextmanager, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
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
inspection = load_module("vm_inspection", HERE / "vm" / "inspect.py")
VALUES = {
    "AZURE_ENV_NAME": "sre-vm-test",
    "AZURE_LOCATION": "australiaeast",
    "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000001",
    "RESOURCE_GROUP": "rg-sre-agent-workshop-sre-vm-test",
    "VM_NAME": "vm-orders-test",
    "NETWORK_SECURITY_GROUP_NAME": "nsg-orders-test",
    "POSTGRESQL_FAULT_RULE_NAME": "PostgreSqlFaultInjection",
    "POSTGRESQL_SERVER_RESOURCE_ID": (
        "/subscriptions/00000000-0000-0000-0000-000000000001/"
        "resourceGroups/rg-sre-agent-workshop-sre-vm-test/"
        "providers/Microsoft.DBforPostgreSQL/flexibleServers/psql-orders-test"
    ),
    "POSTGRESQL_HOST": "psql-orders-test.postgres.database.azure.com",
    "POSTGRESQL_DATABASE": "orders",
    "POSTGRESQL_USER": "vm-orders-test",
    "ORDERS_API_FQDN": "orders-test.australiaeast.cloudapp.azure.com",
    "SERVICE_ORDERS_API_ENDPOINT_URL": "http://orders-test.australiaeast.cloudapp.azure.com:8080",
    "LOG_ANALYTICS_ID": "/workspace",
    "SRE_AGENT_RESOURCE_ID": "/agent",
    "SRE_AGENT_ENDPOINT": "https://sre-test.australiaeast.azuresre.ai",
    "SRE_AGENT_IDENTITY_PRINCIPAL_ID": "30000000-0000-0000-0000-000000000001",
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
        parameters = json.loads(
            (workshop.ROOT / "infra" / "azd" / "main.parameters.json").read_text()
        )
        entrypoint = (workshop.ROOT / "infra" / "azd" / "main.bicep").read_text()
        self.assertNotIn("services", document)
        self.assertEqual(document["workflows"]["up"]["steps"], [{"azd": "provision"}])
        self.assertIn("prepare-azd-env", document["hooks"]["preup"]["windows"]["run"])
        self.assertIn("postprovision", document["hooks"]["postprovision"]["posix"]["run"])
        self.assertIn("predown", document["hooks"]["predown"]["windows"]["run"])
        self.assertIn(workshop.MONITORING_CONTRIBUTOR_ROLE_ID, entrypoint)
        self.assertEqual(
            parameters["parameters"]["workshopResourceGroupName"]["value"],
            "${WORKSHOP_RESOURCE_GROUP_NAME=}",
        )
        self.assertIn("param workshopResourceGroupName string = ''", entrypoint)

    def test_no_legacy_resource_providers(self):
        self.assertIn("Microsoft.Compute", workshop.PROVIDERS)
        self.assertIn("Microsoft.DBforPostgreSQL", workshop.PROVIDERS)
        for namespace in ("Microsoft.Sql", "Microsoft.ContainerRegistry", "Microsoft.KeyVault"):
            self.assertNotIn(namespace, workshop.PROVIDERS)

    def test_deployment_does_not_upload_legacy_agent_content(self):
        source = (HERE / "workshop.py").read_text()
        self.assertNotIn("configure_agent", source)
        self.assertNotIn("knowledge.yaml", source)
        self.assertNotIn("incident-filters.yaml", source)

    def test_service_runs_unprivileged_without_a_database_mount(self):
        service = (HERE / "vm" / "orders-api.service").read_text()
        for setting in (
            "Restart=always", "User=orders", "Group=orders",
            "WantedBy=multi-user.target", "ProtectSystem=strict", "NoNewPrivileges=true",
            "StateDirectory=contoso-orders",
        ):
            self.assertIn(setting, service)
        self.assertNotIn("User=root", service)
        self.assertNotIn("RequiresMountsFor=", service)
        self.assertNotIn("mountpoint", service)

    def test_install_uses_managed_identity_postgresql_and_no_local_server(self):
        source = (HERE / "vm" / "install.sh").read_text()
        for expected in (
            "postgresql-client",
            "OrdersDatabase__Authentication=ManagedIdentity",
            "SSL Mode=VerifyFull",
            "metadata/identity/oauth2/token",
            "ossrdbms-aad.database.windows.net",
            "/dev/tcp/${postgresql_host}/5432",
            '[[ ! -f "${release}/OrdersApi.dll" ]]',
            "export HOME=/root DOTNET_CLI_HOME=/root",
            "--bootstrap",
        ):
            self.assertIn(expected, source)
        for forbidden in ("mkfs", "mount /", "postgresql-16 postgresql"):
            self.assertNotIn(forbidden, source)

    def test_inspection_uses_system_ca_roots_for_libpq(self):
        database = {
            "host": "psql-orders-test.postgres.database.azure.com",
            "port": "5432",
            "database": "orders",
            "username": "vm-orders-test",
        }
        with patch.object(inspection, "access_token", return_value="token"):
            environment = inspection.postgresql_environment(database)
        self.assertEqual(environment["PGSSLMODE"], "verify-full")
        self.assertEqual(environment["PGSSLROOTCERT"], "system")
        self.assertEqual(environment["PGPASSWORD"], "token")

    def test_inspection_reports_and_redacts_subprocess_errors(self):
        secret = "managed-identity-token"
        failure = subprocess.CalledProcessError(
            2,
            ["psql"],
            stderr=f"connection failed for {secret}",
        )
        with patch.object(inspection.subprocess, "run", side_effect=failure), \
                self.assertRaisesRegex(
                    RuntimeError, "psql exited with code 2"
                ) as raised:
            inspection.command(
                "psql",
                environment={"PGPASSWORD": secret},
            )
        self.assertIn("<redacted>", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_payload_is_deterministic_and_excludes_build_outputs_and_secrets(self):
        first, digest = workshop.bundle()
        second, other_digest = workshop.bundle()
        self.assertEqual((first, digest), (second, other_digest))
        self.assertLess(len(first), 50000)
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(base64.b64decode(first)))) as archive:
            names = archive.getnames()
            self.assertIn("app/OrdersApi.csproj", names)
            self.assertIn("app/wwwroot/index.html", names)
            self.assertIn("app/wwwroot/app.css", names)
            self.assertIn("app/wwwroot/app.js", names)
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
        with patch.object(workshop, "az", side_effect=[
            True, {"workshop-architecture": "single-vm-postgresql-v1"},
        ]):
            workshop.check_resource_group(VALUES)

    def test_explicit_resource_group_name_is_used(self):
        values = {**VALUES, "WORKSHOP_RESOURCE_GROUP_NAME": "rg-01"}
        with patch.object(workshop, "az", return_value=False) as az:
            workshop.check_resource_group(values)
        az.assert_called_once_with(
            "group", "exists", "--name", "rg-01",
            "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"],
        )

    def test_invalid_explicit_resource_group_name_is_rejected(self):
        with self.assertRaisesRegex(workshop.DeploymentError, "valid Azure"):
            workshop.resource_group_name({
                **VALUES, "WORKSHOP_RESOURCE_GROUP_NAME": "invalid/name",
            })

    def test_explicit_empty_untagged_group_can_be_provisioned_without_retagging(self):
        values = {**VALUES, "WORKSHOP_RESOURCE_GROUP_NAME": "rg-sre"}
        with patch.object(workshop, "az", side_effect=[True, {}, []]) as azure:
            self.assertTrue(workshop.check_resource_group(values))
        self.assertEqual(azure.call_args_list[-1], call(
            "resource", "list", "--resource-group", "rg-sre",
            "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"], "--query", "[].id"))
        self.assertNotIn("update", str(azure.call_args_list))

    def test_explicit_occupied_or_tagged_group_is_not_adopted(self):
        values = {**VALUES, "WORKSHOP_RESOURCE_GROUP_NAME": "rg-sre"}
        for responses in ([True, {}, ["/existing-resource"]],
                          [True, {"workshop-architecture": "legacy"}],
                          [True, {"owner": "another-workload"}]):
            with self.subTest(responses=responses), patch.object(
                    workshop, "az", side_effect=responses) as azure:
                with self.assertRaisesRegex(workshop.DeploymentError, "not a compatible"):
                    workshop.check_resource_group(values)
                self.assertNotIn("delete", str(azure.call_args_list))
                self.assertNotIn("update", str(azure.call_args_list))

    def test_empty_group_exception_does_not_apply_to_implicit_group_or_bad_inventory(self):
        with patch.object(workshop, "az", side_effect=[True, {}]) as azure:
            with self.assertRaises(workshop.DeploymentError):
                workshop.check_resource_group(VALUES)
            self.assertEqual(azure.call_count, 2)
        values = {**VALUES, "WORKSHOP_RESOURCE_GROUP_NAME": "rg-sre"}
        with patch.object(workshop, "az", side_effect=[True, {}, None]):
            with self.assertRaisesRegex(workshop.DeploymentError, "inventory"):
                workshop.check_resource_group(values)

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

    def test_microsoft_app_registering_does_not_block_available_agent_region(self):
        providers = [
            {
                "namespace": provider,
                "state": "Registering" if provider == "Microsoft.App" else "Registered",
            }
            for provider in workshop.PROVIDERS
        ]

        def azure(*arguments, **_):
            if arguments[:2] == ("provider", "list"):
                return providers
            if arguments[:2] == ("provider", "show"):
                return ["Australia East"]
            self.fail(f"Unexpected Azure call: {arguments}")

        with patch.object(workshop, "az", side_effect=azure) as az, \
                patch.object(workshop.time, "sleep"):
            workshop.register_providers(
                VALUES["AZURE_SUBSCRIPTION_ID"], VALUES["AZURE_LOCATION"],
            )

        self.assertNotIn(
            call(
                "provider", "register",
                "--namespace", "Microsoft.App",
                "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"],
            ),
            az.call_args_list,
        )

    def test_public_ip_feature_is_registered_and_provider_refreshed(self):
        with patch.object(
                workshop, "az",
                side_effect=[
                    {"properties": {"state": "NotRegistered"}},
                    None,
                    {"properties": {"state": "Pending"}},
                    {"properties": {"state": "Registered"}},
                    None,
                ]) as az, patch.object(workshop.time, "sleep"):
            workshop.ensure_public_ip_feature(VALUES)

        self.assertEqual(
            az.call_args_list,
            [
                call(
                    "feature", "show",
                    "--namespace", "Microsoft.Network",
                    "--name", workshop.PUBLIC_IP_FEATURE,
                    "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"],
                ),
                call(
                    "feature", "register",
                    "--namespace", "Microsoft.Network",
                    "--name", workshop.PUBLIC_IP_FEATURE,
                    "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"],
                ),
                call(
                    "feature", "show",
                    "--namespace", "Microsoft.Network",
                    "--name", workshop.PUBLIC_IP_FEATURE,
                    "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"],
                ),
                call(
                    "feature", "show",
                    "--namespace", "Microsoft.Network",
                    "--name", workshop.PUBLIC_IP_FEATURE,
                    "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"],
                ),
                call(
                    "provider", "register",
                    "--namespace", "Microsoft.Network",
                    "--subscription", VALUES["AZURE_SUBSCRIPTION_ID"],
                ),
            ],
        )

    def test_environment_selection_is_explicit_without_mutating_azure_cli(self):
        with patch.dict(os.environ, {"AZURE_ENV_NAME": VALUES["AZURE_ENV_NAME"]}), \
                patch.object(workshop, "cli", return_value=json.dumps(VALUES)) as cli, \
                patch.object(workshop, "az") as az:
            self.assertEqual(workshop.environment(), VALUES)
        cli.assert_called_once_with(
            "azd", "env", "get-values", "--output", "json",
            "--environment", VALUES["AZURE_ENV_NAME"],
        )
        az.assert_not_called()

    def test_windows_azure_cli_preserves_literal_url_and_kql_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "wbin" / "az.cmd"
            launcher.parent.mkdir()
            launcher.touch()
            runtime = root / "python.exe"
            runtime.touch()
            url = "https://management.azure.com/alerts?api-version=2019-03-01&pageCount=20"
            query = "AppRequests | where Name == 'GET /orders' | take 2"
            result = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
            with patch.object(workshop.shutil, "which", return_value=str(launcher)), \
                    patch.object(workshop.sys, "platform", "win32"), \
                    patch.object(workshop.subprocess, "run", return_value=result) as run:
                self.assertEqual(workshop.cli("az", "rest", "--url", url, "--query", query), "{}")
            self.assertEqual(run.call_args.args[0], [
                str(runtime), "-I", "-B", "-m", "azure.cli", "rest", "--url", url,
                "--query", query, "--only-show-errors", "--output", "json"])
            self.assertNotEqual(run.call_args.kwargs.get("shell"), True)

    def test_windows_batch_launcher_without_runtime_fails_before_unsafe_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            launcher = Path(temporary) / "wbin" / "az.cmd"
            launcher.parent.mkdir()
            launcher.touch()
            with patch.object(workshop.shutil, "which", return_value=str(launcher)), \
                    patch.object(workshop.sys, "platform", "win32"), \
                    patch.object(workshop.subprocess, "run") as run, \
                    self.assertRaisesRegex(workshop.DeploymentError, "literal arguments"):
                workshop.cli("az", "rest", "--url", "https://example.invalid/?a=1&b=2")
            run.assert_not_called()

    def test_cohort_exports_are_isolated_per_environment(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.dict(os.environ, {"WORKSHOP_COHORT_MODE": "1"}):
            workshop.export_values(VALUES)
            directory = Path(temporary) / ".workshop" / VALUES["AZURE_ENV_NAME"]
            self.assertTrue((directory / "workshop.env").is_file())
            self.assertTrue((directory / "workshop.ps1").is_file())
            self.assertFalse((Path(temporary) / ".workshop" / "workshop.env").exists())

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
            with self.assertRaisesRegex(workshop.DeploymentError, r"\[postgresql\] invalid schema"):
                with workshop.stage(VALUES, "postgresql"):
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

    def postgresql_capabilities(self, version="16", sku="Standard_B1ms"):
        return [{
            "name": "FlexibleServer",
            "supportedServerVersions": [{"name": version}],
            "supportedServerEditions": [{
                "name": "Burstable",
                "supportedServerSkus": [{"name": sku}],
            }],
        }]

    def test_selected_size_needs_both_regional_and_family_quota(self):
        with patch.object(workshop, "az", side_effect=[[self.sku()], self.quotas()]) as az:
            workshop.check_vm_capacity(VALUES)
        self.assertEqual(az.call_count, 2)
        self.assertTrue(all("--subscription" in args.args for args in az.call_args_list))

    def test_cohort_capacity_multiplies_required_vcpus(self):
        with patch.object(
                workshop, "az",
                side_effect=[[self.sku()], self.quotas(limit=13)]):
            with self.assertRaisesRegex(
                    workshop.DeploymentError, "7 .* need 14 vCPUs"):
                workshop.check_vm_capacity(
                    VALUES, check_existing=False, instances=7,
                )

    def test_capacity_instance_count_must_be_positive(self):
        with self.assertRaisesRegex(workshop.DeploymentError, "positive integer"):
            workshop.check_vm_capacity(
                VALUES, check_existing=False, instances=0,
            )

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

    def test_postgresql_version_and_sku_are_preflighted(self):
        with patch.object(
                workshop, "az",
                return_value=self.postgresql_capabilities()) as az:
            workshop.check_postgresql_capacity(VALUES)
        self.assertEqual(
            az.call_args.args[:3],
            ("postgres", "flexible-server", "list-skus"),
        )
        self.assertIn("--subscription", az.call_args.args)

    def test_postgresql_unavailable_sku_fails_without_deployment(self):
        for capabilities in (
            self.postgresql_capabilities(version="15"),
            self.postgresql_capabilities(sku="Standard_D2s_v3"),
            {"unexpected": "shape"},
        ):
            with self.subTest(capabilities=capabilities), \
                    patch.object(workshop, "az", return_value=capabilities):
                with self.assertRaisesRegex(
                        workshop.DeploymentError,
                        "PostgreSQL Flexible Server capabilities|not advertised"):
                    workshop.check_postgresql_capacity(VALUES)


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
    def test_remove_subscription_monitoring_assignment(self):
        subscription = VALUES["AZURE_SUBSCRIPTION_ID"]
        scope = f"/subscriptions/{subscription}"
        assignment_id = (
            f"{scope}/providers/Microsoft.Authorization/roleAssignments/"
            "40000000-0000-0000-0000-000000000001"
        )
        assignment = {
            "id": assignment_id,
            "scope": scope,
            "roleDefinitionId": (
                f"{scope}/providers/Microsoft.Authorization/roleDefinitions/"
                f"{workshop.MONITORING_CONTRIBUTOR_ROLE_ID}"
            ),
        }
        with patch.object(
                workshop, "az", side_effect=[[assignment], None, []]) as az:
            workshop.remove_monitoring_contributor(VALUES)

        self.assertEqual(
            az.call_args_list[1],
            call(
                "role", "assignment", "delete",
                "--ids", assignment_id,
                "--subscription", subscription,
            ),
        )

    def test_predown_removes_assignment_for_selected_environment(self):
        with patch.object(
                workshop, "environment", return_value=VALUES
        ), patch.object(workshop, "remove_monitoring_contributor") as remove:
            workshop.predown()

        remove.assert_called_once_with(VALUES)

    def test_postprovision_deploys_then_smokes_before_exporting_success(self):
        events = []
        values = {**VALUES, "VM_RESOURCE_ID": "/vm"}
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
                patch.object(workshop, "deploy_vm", side_effect=workshop.DeploymentError("database failed")), \
                patch.object(workshop, "ensure_sre_response_plan") as agent, \
                patch.object(workshop, "smoke") as smoke, patch.object(workshop, "export_values") as export:
            with self.assertRaisesRegex(workshop.DeploymentError, r"\[vm-configuration\] database failed"):
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
        self.assertEqual(calls[0][3], "GET")
        self.assertEqual(calls[1][3], "PUT")
        self.assertEqual(calls[1][4]["properties"]["priorities"], ["Sev1", "Sev2"])
        self.assertEqual(calls[1][4]["properties"]["agentMode"], "Review")
        self.assertEqual(calls[1][4]["properties"]["handlingAgent"], "meta_agent")
        self.assertEqual(calls[1][4]["properties"]["titleContainsAll"], [])
        self.assertEqual(calls[1][4]["properties"]["maxAutomatedInvestigationAttempts"], 3)
        self.assertTrue(calls[1][4]["properties"]["mergeEnabled"])
        self.assertEqual(calls[2][3], "GET")

    def test_transient_response_plan_failure_is_retried(self):
        transient = workshop.SreAgentRequestError(
            "not ready", status=403, retryable=True,
        )
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "sre_agent_token", return_value="token"), \
                patch.object(workshop, "sre_agent_request", side_effect=[
                    transient, {"value": []}, None, {"value": [self.plan()]},
                ]) as request, patch.object(workshop.time, "sleep") as sleep:
            workshop.ensure_sre_response_plan(VALUES, attempts=2, retry_seconds=1)
        self.assertEqual(request.call_count, 4)
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
    def inspection(self, boot_id):
        return {
            "ok": True,
            "bootId": boot_id,
            "database": {
                "provider": "PostgreSQL",
                "serverVersion": "16.4",
                "migrationVersion": 1,
            },
        }

    def test_old_witness_is_replaced_and_bound_to_postgresql_server(self):
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
        self.assertEqual(
            witness["postgresqlServerResourceId"],
            VALUES["POSTGRESQL_SERVER_RESOURCE_ID"],
        )
        self.assertEqual(az.call_args.args[:2], ("vm", "restart"))

    def test_missing_order_on_same_server_is_a_persistence_failure(self):
        witness = {
            "orderId": 1,
            "order": {**ORDER, "orderId": 1},
            "postgresqlServerResourceId": VALUES["POSTGRESQL_SERVER_RESOURCE_ID"],
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

    def test_witness_for_another_server_fails_before_restart(self):
        witness = {
            "orderId": 1,
            "order": {**ORDER, "orderId": 1},
            "postgresqlServerResourceId": (
                VALUES["POSTGRESQL_SERVER_RESOURCE_ID"] + "-other"
            ),
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
            with self.assertRaisesRegex(
                    workshop.DeploymentError, "different PostgreSQL server"):
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
        results = [
            (200, {"status": "ready"}),
            (200, [ORDER] * 5),
            (200, {
                "provider": "PostgreSQL",
                "status": "ready",
                "serverVersion": "16.4",
                "schemaVersion": 1,
                "databaseBytes": 1024,
            }),
        ] + [
            workshop.HttpError(404, path)
            for path in ("/fault/cpu", "/fault/postgresql", "/fault/reset")
        ]
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
            ("postgresql", ["1"]), ("status", ["1"]), ("unknown", []),
            ("reset", ["1"]), ("reset-cpu", ["1"]),
            ("reset-postgresql", ["1"]),
        ):
            with self.subTest(action=action, args=args), \
                    patch.object(workshop, "run_vm") as run, \
                    patch.object(workshop, "az") as az:
                with self.assertRaises(workshop.DeploymentError):
                    workshop.fault(VALUES, action, args)
                run.assert_not_called()
                az.assert_not_called()

    def test_faults_use_control_plane_and_bounded_defaults(self):
        events = []

        def vm_result(_values, action, numbers=()):
            events.append(f"vm:{action}")
            if action == "postgresql-denied":
                return {
                    "ok": True,
                    "cpu": "inactive",
                    "apiRecycled": True,
                    "postgresqlConnectivity": "unavailable",
                    "readinessStatusCode": 503,
                    "ordersStatusCode": 503,
                }
            return {"ok": True, "cpu": "inactive"}

        def deploy_result(_values, inject):
            events.append("postgresql:deny" if inject else "postgresql:allow")
            return {
                "postgresql": "active" if inject else "inactive",
                "postgresqlAccess": "Deny" if inject else "Allow",
            }

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "vm_fault", side_effect=vm_result) as vm, \
                patch.object(workshop, "postgresql_fault_status",
                             return_value={
                                 "postgresql": "inactive",
                                 "postgresqlAccess": "Allow",
                             }), \
                patch.object(workshop, "deploy_postgresql_fault",
                             side_effect=deploy_result) as deploy:
            workshop.fault(VALUES, "cpu", [])
            result = workshop.fault(VALUES, "postgresql", [])
        self.assertEqual(
            vm.call_args_list,
            [
                call(VALUES, "cpu", [300, 2]),
                call(VALUES, "status"),
                call(VALUES, "postgresql-denied"),
            ],
        )
        deploy.assert_called_once_with(VALUES, inject=True)
        self.assertEqual(
            events,
            ["vm:cpu", "vm:status", "postgresql:deny", "vm:postgresql-denied"],
        )
        self.assertTrue(result["apiRecycled"])
        self.assertEqual(result["postgresqlConnectivity"], "unavailable")

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

    def test_cpu_fault_has_a_systemd_ttl(self):
        source = (HERE / "vm" / "faults.py").read_text()
        self.assertEqual(source.count('"--property=RuntimeMaxSec="'), 1)
        self.assertIn("orders-cpu-fault.service", source)

    def test_database_status_requires_consistent_controlled_responses(self):
        with patch.object(faults, "request_json", side_effect=[
                    (200, {"status": "live"}),
                    (503, {"title": "Orders database unavailable"}),
                    (503, {"title": "Orders database unavailable"}),
                ]), patch.object(faults, "api_pid", return_value=42), \
                patch.object(faults, "cpu_status",
                             return_value={"ok": True, "cpu": "inactive"}):
            result = faults.database_status()
        self.assertEqual(result["postgresqlConnectivity"], "unavailable")
        self.assertEqual(result["liveStatusCode"], 200)
        self.assertEqual(result["readinessStatusCode"], 503)
        self.assertEqual(result["ordersStatusCode"], 503)

        with patch.object(faults, "request_json", side_effect=[
                    (200, {"status": "live"}),
                    (503, {"title": "Orders database unavailable"}),
                    (200, []),
                ]):
            with self.assertRaisesRegex(RuntimeError, "inconsistent"):
                faults.database_status()

    def test_postgresql_denial_recycles_until_the_pool_is_drained(self):
        with patch.object(faults, "api_pid", return_value=10), \
                patch.object(faults, "command") as command, \
                patch.object(faults, "wait_for_api_live") as wait_for_live, \
                patch.object(faults, "database_status", side_effect=[
                    {
                        "ok": True,
                        "cpu": "inactive",
                        "apiPid": 20,
                        "postgresqlConnectivity": "ready",
                    },
                    {
                        "ok": True,
                        "cpu": "inactive",
                        "apiPid": 21,
                        "postgresqlConnectivity": "unavailable",
                    },
                ]), patch.object(faults.time, "sleep"):
            result = faults.verify_postgresql_denied()
        self.assertEqual(result["apiRecycleAttempts"], 2)
        self.assertEqual(result["previousApiPid"], 10)
        self.assertTrue(result["apiRecycled"])
        self.assertEqual(wait_for_live.call_count, 2)
        self.assertEqual(
            command.call_args_list,
            [
                call("systemctl", "restart", "orders-api.service"),
                call("systemctl", "restart", "orders-api.service"),
            ],
        )

    def test_postgresql_recovery_does_not_mutate_the_cpu_fault(self):
        with patch.object(faults, "reset_cpu") as reset, patch.object(
                    faults, "recycle_api_and_verify",
                    return_value={
                        "ok": True,
                        "cpu": "active",
                        "postgresqlConnectivity": "ready",
                    }) as recycle:
            result = faults.verify_postgresql_ready()
        reset.assert_not_called()
        recycle.assert_called_once_with("ready")
        self.assertEqual(result["cpu"], "active")
        self.assertEqual(result["postgresqlConnectivity"], "ready")

    def test_postgresql_rule_status_requires_exact_bounded_scope(self):
        rule = {
            "priority": 100,
            "direction": "Outbound",
            "protocol": "Tcp",
            "sourceAddressPrefix": "10.240.0.0/27",
            "sourcePortRange": "*",
            "destinationAddressPrefix": "10.240.0.32/27",
            "destinationPortRange": "5432",
            "access": "Deny",
        }
        with patch.object(workshop, "az", return_value=rule):
            self.assertEqual(
                workshop.postgresql_fault_status(VALUES)["postgresql"], "active")
        for key in ("sourceAddressPrefix", "destinationPortRange", "protocol"):
            with self.subTest(key=key), \
                    patch.object(workshop, "az",
                                 return_value={**rule, key: "unexpected"}):
                with self.assertRaises(workshop.DeploymentError):
                    workshop.postgresql_fault_status(VALUES)

    def test_explicit_all_scenarios_reset_restores_postgresql_then_cpu(self):
        events = []
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "vm_fault",
                             side_effect=lambda _values, action: (
                                 events.append(f"vm:{action}"), {
                                     "ok": True,
                                     "cpu": "inactive",
                                     "apiRecycled": True,
                                     "postgresqlConnectivity": "ready",
                                 }
                             )[1]) as vm, \
                patch.object(workshop, "deploy_postgresql_fault",
                             side_effect=lambda *_args, **_kwargs: (
                                 events.append("postgresql"),
                                 {
                                     "postgresql": "inactive",
                                     "postgresqlAccess": "Allow",
                                 },
                             )[1]) as deploy:
            result = workshop.fault(VALUES, "reset", [])
        deploy.assert_called_once_with(VALUES, inject=False)
        self.assertEqual(
            events,
            ["postgresql", "vm:postgresql-ready", "vm:reset"],
        )
        self.assertEqual(
            vm.call_args_list,
            [
                call(VALUES, "postgresql-ready"),
                call(VALUES, "reset"),
            ],
        )
        self.assertEqual(result["postgresql"], "inactive")
        self.assertEqual(result["postgresqlConnectivity"], "ready")

    def test_scenario_specific_resets_do_not_mutate_the_other_fault(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(
                    workshop, "vm_fault",
                    return_value={
                        "ok": True,
                        "cpu": "active",
                        "apiRecycled": True,
                        "postgresqlConnectivity": "ready",
                    }) as vm, patch.object(
                    workshop, "deploy_postgresql_fault",
                    return_value={
                        "postgresql": "inactive",
                        "postgresqlAccess": "Allow",
                    }) as deploy:
            postgresql = workshop.fault(
                VALUES, "reset-postgresql", [])
        vm.assert_called_once_with(VALUES, "postgresql-ready")
        deploy.assert_called_once_with(VALUES, inject=False)
        self.assertEqual(postgresql["cpu"], "active")

        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(
                    workshop, "vm_fault",
                    return_value={"ok": True, "cpu": "inactive"}) as vm, \
                patch.object(
                    workshop, "postgresql_fault_status",
                    return_value={
                        "postgresql": "active",
                        "postgresqlAccess": "Deny",
                    }) as status, patch.object(
                    workshop, "deploy_postgresql_fault") as deploy:
            cpu = workshop.fault(VALUES, "reset-cpu", [])
        vm.assert_called_once_with(VALUES, "reset")
        status.assert_called_once_with(VALUES)
        deploy.assert_not_called()
        self.assertEqual(cpu["postgresql"], "active")

    def test_injection_records_partial_state_when_recycle_verification_fails(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(workshop, "vm_fault", side_effect=[
                    {"ok": True, "cpu": "inactive"},
                    workshop.DeploymentError("run command interrupted"),
                ]), patch.object(
                    workshop, "deploy_postgresql_fault",
                    return_value={
                        "postgresql": "active",
                        "postgresqlAccess": "Deny",
                    }):
            with self.assertRaisesRegex(
                    workshop.DeploymentError, "operation is partial"):
                workshop.fault(VALUES, "postgresql", [])
            partial = json.loads(
                (workshop.state_directory(VALUES)
                 / "fault-postgresql-partial.json").read_text())
        self.assertFalse(partial["ok"])
        self.assertEqual(partial["postgresqlAccess"], "Deny")
        self.assertEqual(partial["verification"], "failed")

    def test_status_reads_the_rule_before_probing_application_connectivity(self):
        events = []
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "ROOT", Path(temporary)), \
                patch.object(
                    workshop, "postgresql_fault_status",
                    side_effect=lambda _values: (
                        events.append("rule"),
                        {
                            "postgresql": "active",
                            "postgresqlAccess": "Deny",
                        },
                    )[1]), patch.object(
                    workshop, "vm_fault",
                    side_effect=lambda _values, action: (
                        events.append(action),
                        {
                            "ok": True,
                            "cpu": "inactive",
                            "postgresqlConnectivity": "unavailable",
                        },
                    )[1]):
            result = workshop.fault(VALUES, "status", [])
        self.assertEqual(events, ["rule", "postgresql-status"])
        self.assertEqual(result["postgresqlConnectivity"], "unavailable")

    def test_fault_template_matches_normal_deployment_scope(self):
        normal = (workshop.ROOT / "infra" / "network.bicep").read_text()
        fault = (workshop.ROOT / "infra" / "fault.bicep").read_text()
        for expected in (
            "PostgreSqlFaultInjection",
            "10.240.0.0/27",
            "10.240.0.32/27",
            "destinationPortRange: '5432'",
        ):
            self.assertIn(expected, normal)
            self.assertIn(expected, fault)
        self.assertIn("access: 'Allow'", normal)
        self.assertIn("injectPostgresqlFault ? 'Deny' : 'Allow'", fault)


HEALTH_VALUES = {
    **VALUES,
    "SRE_AGENT_RESOURCE_ID": (
        "/subscriptions/00000000-0000-0000-0000-000000000001/"
        "resourceGroups/rg-sre-agent-workshop-sre-vm-test/"
        "providers/Microsoft.App/agents/sre-test"
    ),
}
EXTENDED = "/api/v2/extendedAgent/"
HEALTH_SKILL_PATH = EXTENDED + "skills/proactive-health-check"
HEALTH_AGENT_PATH = EXTENDED + "agents/health-report-investigator"
HEALTH_TASK_PATH = EXTENDED + "scheduledtasks/orders-daily-health-report"
RESPONSE_PLAN_PATH = EXTENDED + "incidentFilters/workshop-sev1-sev2-review"


def connector_response(app_insights="Succeeded", log_analytics="Succeeded"):
    return {"value": [
        {"name": "app-insights", "properties": {
            "dataConnectorType": "AppInsights", "provisioningState": app_insights}},
        {"name": "log-analytics", "properties": {
            "dataConnectorType": "LogAnalytics", "provisioningState": log_analytics}},
    ]}


class FakeSreAgent:
    """In-memory stand-in for the SRE Agent extendedAgent data plane."""

    def __init__(self, stale_task_reads=0, keep_deleted=False, on_put=None):
        self.resources = {}
        self.calls = []
        self.stale_task_reads = stale_task_reads
        self.keep_deleted = keep_deleted
        self.on_put = on_put or {}

    def request(self, endpoint, token, path, method="GET", body=None):
        self.calls.append((method, path))
        listing = EXTENDED + "scheduledtasks"
        if method == "PUT":
            stored = copy.deepcopy(body)
            if path in self.on_put:
                self.on_put[path](stored)
            self.resources[path] = stored
            return None
        if method == "DELETE":
            if path not in self.resources:
                raise workshop.SreAgentRequestError("not found", status=404, retryable=True)
            if not self.keep_deleted:
                del self.resources[path]
            return None
        if path == listing:
            if self.stale_task_reads:
                self.stale_task_reads -= 1
                return {"value": []}
            return {"value": [item for key, item in self.resources.items()
                              if key.startswith(listing + "/")]}
        if path in self.resources:
            return self.resources[path]
        raise workshop.SreAgentRequestError("not found", status=404, retryable=True)

    def methods(self, method):
        return [path for name, path in self.calls if name == method]


class HealthCheckTests(unittest.TestCase):
    @contextmanager
    def patched(self, fake, connectors=None):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(workshop, "ROOT", Path(temporary)))
            self.az = stack.enter_context(patch.object(
                workshop, "az", return_value=connectors or connector_response()))
            stack.enter_context(patch.object(
                workshop, "sre_agent_token", return_value="secret-token", autospec=True))
            self.request = stack.enter_context(patch.object(
                workshop, "sre_agent_request", side_effect=fake.request))
            self.sleep = stack.enter_context(patch.object(workshop.time, "sleep"))
            yield Path(temporary)

    def definition_with(self, mutate):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copied = root / "agent"
            shutil.copytree(workshop.AGENT_DEFINITIONS, copied)
            template = copied / "scheduled-tasks" / "orders-daily-health-report.yaml"
            document = yaml.safe_load(template.read_text(encoding="utf-8"))
            mutate(document, root)
            template.write_text(yaml.safe_dump(document), encoding="utf-8")
            with patch.object(workshop, "AGENT_DEFINITIONS", copied):
                return workshop.load_health_check_definition(template)

    def test_definition_is_a_review_mode_read_only_task(self):
        definition = workshop.load_health_check_definition()
        skills, agent, task = workshop.health_check_documents(
            definition, ["QueryLogAnalyticsByWorkspaceId"])
        self.assertEqual(definition["name"], "orders-daily-health-report")
        self.assertEqual(definition["agent"], "health-report-investigator")
        self.assertEqual(task["properties"]["agentMode"], "Review")
        self.assertTrue(task["properties"]["isEnabled"])
        self.assertEqual(task["properties"]["agent"], agent["name"])
        self.assertEqual(agent["properties"]["allowedSkills"], [skills[0]["name"]])
        self.assertTrue(set(definition["tools"]) <= workshop.HEALTH_CHECK_TOOLS)
        for forbidden in ("RunAzCliWriteCommands", "SendOutlookEmail", "CreateGithubIssue"):
            self.assertNotIn(forbidden, agent["properties"]["tools"])
        self.assertEqual(skills[0]["properties"]["name"], "proactive-health-check")
        self.assertIn("name: proactive-health-check", skills[0]["properties"]["skillContent"])

    def test_schedule_is_weekday_morning_new_zealand_standard_time_in_utc(self):
        schedule = workshop.load_health_check_definition()["schedule"]
        minute, hour, day_of_month, month, day_of_week = schedule.split()
        self.assertEqual(schedule, "0 21 * * 0-4")
        self.assertEqual((minute, day_of_month, month), ("0", "*", "*"))
        sunday_evening = datetime(2026, 10, 11, int(hour), int(minute), tzinfo=timezone.utc)
        self.assertEqual(sunday_evening.weekday(), 6)
        monday_morning = sunday_evening + timedelta(hours=12)
        self.assertEqual((monday_morning.weekday(), monday_morning.hour), (0, 9))
        self.assertEqual(day_of_week, "0-4")

    def test_skill_uses_workload_names_that_the_deployment_defines(self):
        skill = (workshop.AGENT_DEFINITIONS / "skills" / "proactive-health-check"
                 / "SKILL.md").read_text(encoding="utf-8")
        alerts = (workshop.ROOT / "infra" / "alerts.bicep").read_text(encoding="utf-8")
        alert_names = re.findall(r"name: '(alert-orders-[a-z0-9-]+)'", alerts)
        self.assertEqual(len(alert_names), 3)
        fault = (workshop.ROOT / "infra" / "fault.bicep").read_text(encoding="utf-8")
        availability = (workshop.ROOT / "src" / "OrdersApi"
                        / "DatabaseAvailabilityService.cs").read_text(encoding="utf-8")
        self.assertIn('Name = "orders-api-postgresql"', availability)
        self.assertIn("name: 'PostgreSqlFaultInjection'", fault)
        for expected in (*alert_names, "PostgreSqlFaultInjection", "orders-api-postgresql",
                         'AppRoleName == "orders-api"', 'DependencyType == "PostgreSQL"',
                         "AppAvailabilityResults", "Heartbeat", "Perf",
                         'datetime_utc_to_local(TimeGenerated, "Pacific/Auckland")'):
            self.assertIn(expected, skill)

    def test_definition_files_use_no_em_dashes(self):
        for path in (
            workshop.HEALTH_CHECK_TEMPLATE,
            workshop.AGENT_DEFINITIONS / "skills" / "proactive-health-check" / "SKILL.md",
        ):
            self.assertNotIn("\u2014", path.read_text(encoding="utf-8"), path.name)

    def test_unsafe_or_malformed_definitions_are_rejected(self):
        def outside_skill(document, root):
            (root / "outside.md").write_text(
                "---\nname: proactive-health-check\ndescription: Outside the agent directory.\n"
                "---\nBody\n", encoding="utf-8")
            document["custom_agent"]["skills"][0]["source"] = "../../outside.md"

        cases = {
            "write tool attached": (
                lambda d, _: d["custom_agent"]["tools"]["read_only"].append("RunAzCliWriteCommands"),
                "approved read-only tools"),
            "email tool attached": (
                lambda d, _: d["custom_agent"]["tools"]["read_only"].append("SendOutlookEmail"),
                "approved read-only tools"),
            "approval group": (
                lambda d, _: d["custom_agent"]["tools"].update(ask_approval=["SendOutlookEmail"]),
                "only read_only and deny"),
            "write tool not denied": (
                lambda d, _: d["custom_agent"]["tools"]["deny"].remove("RunAzCliWriteCommands"),
                "must be denied"),
            "autonomous task": (
                lambda d, _: d["trigger"].update(action_mode="Autonomous"), "Review mode"),
            "disabled task": (lambda d, _: d["trigger"].update(enabled=False), "enabled"),
            "named schedule": (lambda d, _: d["trigger"].update(schedule="@daily"), "five-field"),
            "six-field schedule": (
                lambda d, _: d["trigger"].update(schedule="0 0 21 * * 0-4"), "five-field"),
            "wrong handler": (
                lambda d, _: d["trigger"].update(handling_agent="meta-agent"), "must match"),
            "missing skill file": (
                lambda d, _: d["custom_agent"]["skills"][0].update(source="../skills/missing.md"),
                "must be a file under"),
            "skill outside definitions": (outside_skill, "must be a file under"),
            "skill name mismatch": (
                lambda d, _: d["custom_agent"]["skills"][0].update(name="another-skill"),
                "matching name"),
        }
        for label, (mutate, expected) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(workshop.DeploymentError, expected):
                self.definition_with(mutate)

    def test_install_writes_skill_agent_then_task_and_verifies_read_back(self):
        fake = FakeSreAgent()
        with self.patched(fake) as temporary:
            result = workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
            evidence = json.loads(
                (workshop.state_directory(HEALTH_VALUES) / "scheduled-health-check.json").read_text())
        self.assertEqual(
            fake.methods("PUT"), [HEALTH_SKILL_PATH, HEALTH_AGENT_PATH, HEALTH_TASK_PATH])
        self.assertTrue(result["ok"] and result["installed"] and result["matchesDefinition"])
        self.assertEqual(evidence, result)
        self.assertEqual(result["scheduledTask"]["cronExpression"], "0 21 * * 0-4")
        self.assertEqual(result["scheduledTask"]["agentMode"], "Review")
        self.assertTrue(result["scheduledTask"]["enabled"])
        self.assertEqual(result["problems"], [])
        self.assertNotIn("secret-token", json.dumps(evidence))
        self.assertTrue(str(self.az.call_args.args[-1]).endswith(
            "/providers/Microsoft.App/agents/sre-test/DataConnectors?api-version=2025-05-01-preview"))
        tools = fake.resources[HEALTH_AGENT_PATH]["properties"]["tools"]
        self.assertEqual(tools, [
            "GetAzCliHelp", "RunAzCliReadCommands", "PlotAreaChartWithCorrelation",
            "PlotBarChart", "QueryLogAnalyticsByWorkspaceId", "QueryAppInsightsUsingAppId"])
        self.assertNotIn(RESPONSE_PLAN_PATH, fake.resources)
        self.assertEqual(self.request.call_args_list[0].args[0], VALUES["SRE_AGENT_ENDPOINT"])

    def test_install_is_idempotent(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
            first = copy.deepcopy(fake.resources)
            workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
        self.assertEqual(fake.resources, first)
        self.assertEqual(len(fake.resources), 3)

    def test_only_tools_for_healthy_connectors_are_attached(self):
        fake = FakeSreAgent()
        with self.patched(fake, connector_response(app_insights="Failed")):
            workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
        self.assertEqual(
            fake.resources[HEALTH_AGENT_PATH]["properties"]["tools"][-1],
            "QueryLogAnalyticsByWorkspaceId")
        self.assertNotIn(
            "QueryAppInsightsUsingAppId", fake.resources[HEALTH_AGENT_PATH]["properties"]["tools"])

    def test_unhealthy_log_analytics_connector_fails_before_any_write(self):
        fake = FakeSreAgent()
        with self.patched(fake, connector_response(log_analytics="Failed")):
            with self.assertRaisesRegex(workshop.DeploymentError, "log-analytics connector"):
                workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
        self.request.assert_not_called()
        self.assertEqual(fake.calls, [])

    def test_unexpected_agent_resource_id_is_rejected_before_any_azure_call(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            with self.assertRaisesRegex(workshop.DeploymentError, "resource identifier"):
                workshop.ensure_health_check({**HEALTH_VALUES, "SRE_AGENT_RESOURCE_ID": "/agent"})
        self.az.assert_not_called()
        self.request.assert_not_called()

    def test_endpoint_outside_the_sre_agent_host_is_rejected(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            with self.assertRaisesRegex(workshop.DeploymentError, "Unexpected Azure SRE Agent endpoint"):
                workshop.ensure_health_check(
                    {**HEALTH_VALUES, "SRE_AGENT_ENDPOINT": "https://example.com"})
        self.request.assert_not_called()

    def test_slow_visibility_is_retried(self):
        fake = FakeSreAgent(stale_task_reads=1)
        with self.patched(fake):
            result = workshop.ensure_health_check(HEALTH_VALUES, attempts=2, retry_seconds=1)
        self.assertTrue(result["matchesDefinition"])
        self.sleep.assert_called_once_with(1)
        self.assertEqual(fake.methods("PUT").count(HEALTH_TASK_PATH), 2)

    def test_permanent_failure_is_not_retried_and_leaves_no_evidence(self):
        permanent = workshop.SreAgentRequestError("bad request", status=400, retryable=False)
        with self.patched(FakeSreAgent()) as temporary:
            self.request.side_effect = permanent
            with self.assertRaisesRegex(workshop.DeploymentError, "bad request"):
                workshop.ensure_health_check(HEALTH_VALUES)
            evidence = workshop.state_directory(HEALTH_VALUES) / "scheduled-health-check.json"
            self.assertFalse(evidence.exists())
        self.request.assert_called_once()
        self.sleep.assert_not_called()

    def test_a_schedule_the_agent_does_not_keep_fails_after_bounded_attempts(self):
        fake = FakeSreAgent(on_put={
            HEALTH_TASK_PATH: lambda body: body["properties"].update(cronExpression="0 9 * * 1-5"),
        })
        with self.patched(fake) as temporary:
            with self.assertRaisesRegex(workshop.DeploymentError, "cronExpression"):
                workshop.ensure_health_check(HEALTH_VALUES, attempts=2, retry_seconds=1)
            evidence = workshop.state_directory(HEALTH_VALUES) / "scheduled-health-check.json"
            self.assertFalse(evidence.exists())
        self.sleep.assert_called_once_with(1)

    def test_status_is_read_only_and_reports_an_absent_health_check(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            report = workshop.health_check_status(HEALTH_VALUES)
        self.assertFalse(report["installed"])
        self.assertFalse(report["matchesDefinition"])
        self.assertIsNone(report["scheduledTask"])
        self.assertIsNone(report["customAgent"])
        self.assertTrue(report["problems"])
        self.assertEqual({method for method, _ in fake.calls}, {"GET"})
        self.az.assert_not_called()

    def test_status_reports_a_matching_install_and_portal_edits(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
            matching = workshop.health_check_status(HEALTH_VALUES)
            fake.resources[HEALTH_TASK_PATH]["properties"]["cronExpression"] = "0 20 * * 0-4"
            fake.resources[HEALTH_TASK_PATH]["properties"]["isEnabled"] = False
            fake.resources[HEALTH_AGENT_PATH]["properties"]["tools"].append("RunAzCliWriteCommands")
            edited = workshop.health_check_status(HEALTH_VALUES)
        self.assertTrue(matching["installed"] and matching["matchesDefinition"])
        self.assertTrue(edited["installed"])
        self.assertFalse(edited["matchesDefinition"])
        problems = " ".join(edited["problems"])
        for expected in ("cronExpression", "not enabled", "RunAzCliWriteCommands"):
            self.assertIn(expected, problems)

    def test_install_fails_when_the_agent_does_not_keep_a_safety_property(self):
        def tools_without(name):
            return lambda body: body["properties"]["tools"].remove(name)

        cases = {
            "autonomous mode": (
                HEALTH_TASK_PATH, lambda b: b["properties"].update(agentMode="Autonomous"),
                "agentMode"),
            "another handler": (
                HEALTH_TASK_PATH, lambda b: b["properties"].update(agent="meta_agent"),
                "Scheduled task agent"),
            "disabled task": (
                HEALTH_TASK_PATH, lambda b: b["properties"].update(isEnabled=False), "not enabled"),
            "changed prompt": (
                HEALTH_TASK_PATH, lambda b: b["properties"].update(agentPrompt="Restart the VM."),
                "property agentPrompt differs"),
            "missing chart tool": (
                HEALTH_AGENT_PATH, tools_without("PlotBarChart"), "missing tools: PlotBarChart"),
            "missing log analytics tool": (
                HEALTH_AGENT_PATH, tools_without("QueryLogAnalyticsByWorkspaceId"),
                "missing tools: QueryLogAnalyticsByWorkspaceId"),
            "extra write tool": (
                HEALTH_AGENT_PATH,
                lambda b: b["properties"]["tools"].append("RunAzCliWriteCommands"),
                "outside the read-only set"),
            "skill not allowed": (
                HEALTH_AGENT_PATH, lambda b: b["properties"].update(allowedSkills=[]),
                "does not allow skills"),
            "mcp tools": (
                HEALTH_AGENT_PATH, lambda b: b["properties"].update(mcpTools=["anything"]),
                "property mcpTools"),
            "connectors": (
                HEALTH_AGENT_PATH, lambda b: b["properties"].update(connectors=["anything"]),
                "property connectors"),
            "agents as tools": (
                HEALTH_AGENT_PATH, lambda b: b["properties"].update(agentsAsTools=["anything"]),
                "property agentsAsTools"),
            "common tools": (
                HEALTH_AGENT_PATH, lambda b: b["properties"].update(commonTools=["anything"]),
                "property commonTools"),
            "system skills": (
                HEALTH_AGENT_PATH, lambda b: b["properties"].update(addSystemSkills=True),
                "addSystemSkills"),
            "changed instructions": (
                HEALTH_AGENT_PATH,
                lambda b: b["properties"].update(instructions="Restart the VM."),
                "property instructions differs"),
            "changed skill body": (
                HEALTH_SKILL_PATH,
                lambda b: b["properties"].update(skillContent="Restart the VM."),
                "content differs"),
        }
        for label, (path, mutate, expected) in cases.items():
            with self.subTest(label):
                with self.patched(FakeSreAgent(on_put={path: mutate})):
                    with self.assertRaisesRegex(workshop.DeploymentError, expected):
                        workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
                    evidence = workshop.state_directory(HEALTH_VALUES) / "scheduled-health-check.json"
                    self.assertFalse(evidence.exists())

    def test_agent_document_pins_optional_tool_categories_empty(self):
        definition = workshop.load_health_check_definition()
        _, agent, _ = workshop.health_check_documents(definition, ["QueryLogAnalyticsByWorkspaceId"])
        properties = agent["properties"]
        for key in ("mcpTools", "connectors", "agentsAsTools", "commonTools"):
            self.assertEqual(properties[key], [], key)
        self.assertIs(properties["addSystemSkills"], False)

    def test_status_reports_changes_to_the_text_that_defines_behavior(self):
        edits = {
            "instructions": (
                HEALTH_AGENT_PATH, lambda p: p.update(instructions="Restart the VM if CPU is high.")),
            "prompt": (HEALTH_TASK_PATH, lambda p: p.update(agentPrompt="Restart the VM.")),
            "skill": (HEALTH_SKILL_PATH, lambda p: p.update(skillContent="Restart the VM.")),
            "log analytics tool": (
                HEALTH_AGENT_PATH, lambda p: p["tools"].remove("QueryLogAnalyticsByWorkspaceId")),
            "system skills": (HEALTH_AGENT_PATH, lambda p: p.update(addSystemSkills=True)),
        }
        for label, (path, edit) in edits.items():
            with self.subTest(label):
                fake = FakeSreAgent()
                with self.patched(fake):
                    workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
                    edit(fake.resources[path]["properties"])
                    report = workshop.health_check_status(HEALTH_VALUES)
                self.assertTrue(report["installed"])
                self.assertFalse(report["matchesDefinition"])
                self.assertTrue(report["problems"])

    def test_status_tolerates_how_the_service_returns_text(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
            skill = fake.resources[HEALTH_SKILL_PATH]["properties"]
            body = skill.pop("skillContent").split("---\n", 2)[2]
            skill["skillMdContent"] = body.replace("\n", "\r\n") + "\r\n"
            fake.resources[HEALTH_AGENT_PATH]["properties"].pop("instructions")
            fake.resources[HEALTH_TASK_PATH]["properties"]["agentPrompt"] += "  \n"
            report = workshop.health_check_status(HEALTH_VALUES)
        self.assertTrue(report["matchesDefinition"], report["problems"])

    def test_remove_deletes_only_its_own_resources_in_dependency_order(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            fake.resources[RESPONSE_PLAN_PATH] = {"name": "workshop-sev1-sev2-review"}
            fake.resources[EXTENDED + "scheduledtasks/someone-elses-task"] = {
                "name": "someone-elses-task", "properties": {}}
            workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
            evidence = workshop.state_directory(HEALTH_VALUES) / "scheduled-health-check.json"
            self.assertTrue(evidence.exists())
            result = workshop.remove_health_check(HEALTH_VALUES, attempts=1)
            self.assertFalse(evidence.exists())
        self.assertEqual(
            fake.methods("DELETE"), [HEALTH_TASK_PATH, HEALTH_AGENT_PATH, HEALTH_SKILL_PATH])
        self.assertEqual(
            set(fake.resources),
            {EXTENDED + "scheduledtasks/someone-elses-task", RESPONSE_PLAN_PATH})
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["removed"],
            ["orders-daily-health-report", "health-report-investigator", "proactive-health-check"])

    def test_remove_succeeds_when_nothing_is_installed(self):
        fake = FakeSreAgent()
        with self.patched(fake):
            result = workshop.remove_health_check(HEALTH_VALUES, attempts=1)
        self.assertTrue(result["ok"])
        self.assertEqual(len(fake.methods("DELETE")), 3)

    def test_remove_fails_when_resources_stay_visible(self):
        fake = FakeSreAgent(keep_deleted=True)
        with self.patched(fake):
            workshop.ensure_health_check(HEALTH_VALUES, attempts=1)
            with self.assertRaisesRegex(workshop.DeploymentError, "still visible"):
                workshop.remove_health_check(HEALTH_VALUES, attempts=2, retry_seconds=1)
        self.sleep.assert_called_once_with(1)

    def test_each_action_runs_exactly_one_handler(self):
        names = ("ensure_health_check", "health_check_status", "remove_health_check")
        for action, expected in zip(workshop.HEALTH_CHECK_ACTIONS, names):
            with self.subTest(action), ExitStack() as stack:
                handlers = {
                    name: stack.enter_context(
                        patch.object(workshop, name, return_value={"handler": name}))
                    for name in names
                }
                self.assertEqual(
                    workshop.health_check(HEALTH_VALUES, action), {"handler": expected})
                for name, handler in handlers.items():
                    if name == expected:
                        handler.assert_called_once_with(HEALTH_VALUES)
                    else:
                        handler.assert_not_called()

    def test_command_accepts_exactly_one_known_action(self):
        with self.assertRaisesRegex(workshop.DeploymentError, "install, status, or remove"):
            workshop.health_check(HEALTH_VALUES, "bogus")

        def run(*arguments):
            with patch.object(sys, "argv", ["workshop.py", "health-check", *arguments]), \
                    patch.object(workshop, "environment", return_value=HEALTH_VALUES), \
                    patch.object(workshop, "health_check", return_value={"ok": True}) as handler, \
                    redirect_stdout(io.StringIO()):
                workshop.main()
            return handler

        run().assert_called_once_with(HEALTH_VALUES, "status")
        run("install").assert_called_once_with(HEALTH_VALUES, "install")
        with self.assertRaisesRegex(workshop.DeploymentError, "one action"):
            run("install", "remove")

    def test_azd_up_does_not_install_the_health_check(self):
        self.assertNotIn("health_check", inspect.getsource(workshop.postprovision))
        self.assertNotIn("health", (workshop.ROOT / "azure.yaml").read_text(encoding="utf-8"))
        self.assertIn("scheduled-health-check.json", workshop.FRESH_ENVIRONMENT_EVIDENCE)


class FakeKnowledgeBase:
    """In-memory stand-in for the SRE Agent AgentMemory data plane."""

    def __init__(self, hidden_reads=0, others=()):
        self.files = {name: {"filename": name, "fileSize": 10, "isIndexed": True} for name in others}
        self.hidden_reads = hidden_reads
        self.uploads = []
        self.deletes = []

    def request(self, endpoint, token, path, method="GET", body=None, **options):
        base = workshop.KNOWLEDGE_BASE
        if method == "POST" and path == base + "upload":
            fields, field, name, content_type, content = options["multipart"]
            self.uploads.append((fields, field, name, content_type, content))
            self.files[name] = {"filename": name, "fileSize": len(content), "isIndexed": False}
            return None
        if method == "GET" and path == base + "files":
            if self.hidden_reads:
                self.hidden_reads -= 1
                return {"files": [item for item in self.files.values()
                                  if not item["filename"].startswith("workshop-")]}
            return {"files": list(self.files.values())}
        if method == "DELETE" and path.startswith(base + "document/"):
            name = path.rsplit("/", 1)[1]
            self.deletes.append(name)
            if self.files.pop(name, None) is None:
                raise workshop.SreAgentRequestError("missing", status=404, retryable=True)
            return None
        raise AssertionError(f"Unexpected request {method} {path}")


class KnowledgeTests(unittest.TestCase):
    DOCUMENTS = ("workshop-escalation-and-comms.md", "workshop-slo-and-baselines.md")

    @contextmanager
    def patched(self, fake):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(workshop, "ROOT", Path(temporary)))
            stack.enter_context(patch.object(
                workshop, "sre_agent_token", return_value="secret-token", autospec=True))
            stack.enter_context(patch.object(
                workshop, "sre_agent_request", side_effect=fake.request))
            self.sleep = stack.enter_context(patch.object(workshop.time, "sleep"))
            yield Path(temporary)

    def test_repository_files_are_fictitious_lf_text_the_upload_accepts(self):
        documents = workshop.load_knowledge_documents()
        self.assertEqual(tuple(documents), self.DOCUMENTS)
        for name, content in documents.items():
            with self.subTest(name):
                self.assertNotIn(b"\r", content)
                self.assertIn(b"Fictitious demo data", content)
                self.assertNotRegex(content.decode("utf-8"), r"@(?!example\.com)[\w.-]+\.\w+")

    def test_rejects_bad_names_empty_and_oversized_files(self):
        for name, content, message in (
            ("notes.md", b"text", "must be named"),
            ("workshop-Upper.md", b"text", "must be named"),
            ("workshop-empty.md", b"  \n", "1 byte to 16 MB"),
            ("workshop-binary.md", b"\xff\xfe", "UTF-8"),
        ):
            with self.subTest(name), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / "knowledge"
                directory.mkdir()
                (directory / name).write_bytes(content)
                with patch.object(workshop, "AGENT_DEFINITIONS", Path(temporary)):
                    with self.assertRaisesRegex(workshop.DeploymentError, message):
                        workshop.load_knowledge_documents()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "AGENT_DEFINITIONS", Path(temporary)):
            (Path(temporary) / "knowledge").mkdir()
            with self.assertRaisesRegex(workshop.DeploymentError, "No workshop"):
                workshop.load_knowledge_documents()
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(workshop, "AGENT_DEFINITIONS", Path(temporary)), \
                patch.object(workshop, "KNOWLEDGE_MAX_BYTES", 3):
            (Path(temporary) / "knowledge").mkdir()
            (Path(temporary) / "knowledge" / "workshop-big.md").write_bytes(b"abcd")
            with self.assertRaisesRegex(workshop.DeploymentError, "1 byte to 16 MB"):
                workshop.load_knowledge_documents()

    def test_multipart_body_carries_the_files_part_and_indexing_flag(self):
        body, content_type = workshop.sre_agent_multipart(
            {"triggerIndexing": "true"}, "files", "workshop-a.md", "text/plain", b"# A\n")
        boundary = content_type.split("boundary=")[1]
        self.assertTrue(content_type.startswith("multipart/form-data; "))
        text = body.decode("utf-8")
        self.assertIn('name="triggerIndexing"\r\n\r\ntrue\r\n', text)
        self.assertIn(
            'name="files"; filename="workshop-a.md"\r\nContent-Type: text/plain\r\n\r\n# A\n\r\n',
            text)
        self.assertTrue(text.endswith(f"--{boundary}--\r\n"))

    def test_install_uploads_every_file_and_records_evidence(self):
        fake = FakeKnowledgeBase(others=("customer-notes.md",))
        with self.patched(fake) as root:
            result = workshop.ensure_knowledge(HEALTH_VALUES, attempts=1)
            evidence = json.loads(
                (root / ".workshop" / HEALTH_VALUES["AZURE_ENV_NAME"] / "knowledge-base.json")
                .read_text(encoding="utf-8"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["installed"])
        self.assertFalse(result["indexed"])
        self.assertEqual(tuple(result["documents"]), self.DOCUMENTS)
        self.assertEqual(evidence["documents"].keys(), result["documents"].keys())
        self.assertEqual([upload[2] for upload in fake.uploads], list(self.DOCUMENTS))
        for fields, field, _, content_type, _ in fake.uploads:
            self.assertEqual((fields, field, content_type),
                             ({"triggerIndexing": "true"}, "files", "text/plain"))
        self.assertIn("customer-notes.md", fake.files)

    def test_install_is_idempotent_and_waits_for_the_listing(self):
        fake = FakeKnowledgeBase(hidden_reads=1)
        with self.patched(fake):
            result = workshop.ensure_knowledge(HEALTH_VALUES, attempts=2, retry_seconds=1)
            again = workshop.ensure_knowledge(HEALTH_VALUES, attempts=1)
        self.assertTrue(result["installed"] and again["installed"])
        self.assertEqual(len(fake.files), 2)
        self.assertEqual(len(fake.uploads), 6)
        self.sleep.assert_called_once_with(1)

    def test_install_fails_when_files_never_appear(self):
        fake = FakeKnowledgeBase(hidden_reads=5)
        with self.patched(fake):
            with self.assertRaisesRegex(workshop.SreAgentRequestError, "not visible yet"):
                workshop.ensure_knowledge(HEALTH_VALUES, attempts=1)

    def test_status_is_read_only(self):
        fake = FakeKnowledgeBase()
        with self.patched(fake):
            absent = workshop.knowledge_status(HEALTH_VALUES)
            workshop.ensure_knowledge(HEALTH_VALUES, attempts=1)
            fake.uploads.clear()
            present = workshop.knowledge_status(HEALTH_VALUES)
        self.assertFalse(absent["installed"])
        self.assertTrue(present["installed"])
        self.assertEqual(fake.uploads, [])
        self.assertEqual(fake.deletes, [])

    def test_remove_deletes_only_the_repository_files(self):
        fake = FakeKnowledgeBase(others=("customer-notes.md", "workshop-someone-elses.md"))
        with self.patched(fake) as root:
            workshop.ensure_knowledge(HEALTH_VALUES, attempts=1)
            result = workshop.remove_knowledge(HEALTH_VALUES, attempts=1)
            evidence = root / ".workshop" / HEALTH_VALUES["AZURE_ENV_NAME"] / "knowledge-base.json"
            self.assertFalse(evidence.exists())
        self.assertEqual(result["removed"], list(self.DOCUMENTS))
        self.assertEqual(sorted(fake.deletes), list(self.DOCUMENTS))
        self.assertEqual(sorted(fake.files), ["customer-notes.md", "workshop-someone-elses.md"])

    def test_remove_tolerates_files_that_are_already_gone(self):
        fake = FakeKnowledgeBase()
        with self.patched(fake):
            result = workshop.remove_knowledge(HEALTH_VALUES, attempts=1)
        self.assertTrue(result["ok"])

    def test_remove_reports_files_that_stay_listed(self):
        fake = FakeKnowledgeBase()
        stuck = {name: {"indexed": True, "size": 1} for name in self.DOCUMENTS}
        with self.patched(fake), patch.object(workshop, "knowledge_listing", return_value=stuck):
            with self.assertRaisesRegex(workshop.SreAgentRequestError, "still listed"):
                workshop.remove_knowledge(HEALTH_VALUES, attempts=1)

    def test_each_action_runs_exactly_one_handler(self):
        names = ("ensure_knowledge", "knowledge_status", "remove_knowledge")
        for action, expected in zip(workshop.KNOWLEDGE_ACTIONS, names):
            with self.subTest(action), ExitStack() as stack:
                handlers = {
                    name: stack.enter_context(
                        patch.object(workshop, name, return_value={"handler": name}))
                    for name in names
                }
                self.assertEqual(workshop.knowledge(HEALTH_VALUES, action), {"handler": expected})
                for name, handler in handlers.items():
                    if name == expected:
                        handler.assert_called_once_with(HEALTH_VALUES)
                    else:
                        handler.assert_not_called()

    def test_command_accepts_exactly_one_known_action(self):
        with self.assertRaisesRegex(workshop.DeploymentError, "install, status, or remove"):
            workshop.knowledge(HEALTH_VALUES, "bogus")

        def run(*arguments):
            with patch.object(sys, "argv", ["workshop.py", "knowledge", *arguments]), \
                    patch.object(workshop, "environment", return_value=HEALTH_VALUES), \
                    patch.object(workshop, "knowledge", return_value={"ok": True}) as handler, \
                    redirect_stdout(io.StringIO()):
                workshop.main()
            return handler

        run().assert_called_once_with(HEALTH_VALUES, "status")
        run("install").assert_called_once_with(HEALTH_VALUES, "install")
        with self.assertRaisesRegex(workshop.DeploymentError, "one action"):
            run("install", "remove")

    def test_azd_up_does_not_install_knowledge(self):
        self.assertNotIn("knowledge", inspect.getsource(workshop.postprovision))
        self.assertNotIn("knowledge", (workshop.ROOT / "azure.yaml").read_text(encoding="utf-8"))
        self.assertIn("knowledge-base.json", workshop.FRESH_ENVIRONMENT_EVIDENCE)


class HealthCheckDocumentationTests(unittest.TestCase):
    REPO = HERE.parent
    MODULE = ("docs", "sre", "05-scheduled-health-check", "index.md")

    def read(self, *parts):
        return self.REPO.joinpath(*parts).read_text(encoding="utf-8")

    def test_module_replaces_review_and_sits_between_the_incidents_and_cleanup(self):
        self.assertTrue(self.REPO.joinpath(*self.MODULE).is_file())
        self.assertFalse((self.REPO / "docs" / "sre" / "05-review-and-improve").exists())
        navigation = self.read("mkdocs.yml")
        self.assertIn("sre/05-scheduled-health-check/index.md", navigation)
        self.assertNotIn("05-review-and-improve", navigation)
        module = self.read(*self.MODULE)
        self.assertIn("(../04-incident-postgresql/index.md)", module)
        self.assertIn("(../06-cleanup/index.md)", module)
        for neighbour in (
            ("docs", "sre", "04-incident-postgresql", "index.md"),
            ("docs", "sre", "06-cleanup", "index.md"),
        ):
            self.assertIn("(../05-scheduled-health-check/index.md)", self.read(*neighbour))

    def test_documented_health_check_commands_exist_and_every_action_is_documented(self):
        pattern = re.compile(r"workshop\.py health-check(?: (\w+))?")
        documented = set()
        for parts in (
            ("README.md",), self.MODULE,
            ("docs", "sre", "06-cleanup", "index.md"),
            ("docs", "sre", "30-appendix", "01-variables.md"),
            ("docs", "sre", "30-appendix", "03-cost-management.md"),
        ):
            documented |= {match.group(1) for match in pattern.finditer(self.read(*parts))}
        self.assertNotIn(None, documented)
        self.assertEqual(documented, set(workshop.HEALTH_CHECK_ACTIONS))

    def test_schedule_is_stated_consistently_with_the_definition(self):
        schedule = workshop.load_health_check_definition()["schedule"]
        for parts in (
            ("README.md",), ("CONTRIBUTING.md",), self.MODULE,
            ("docs", "sre", "30-appendix", "02-troubleshooting.md"),
        ):
            self.assertIn(schedule, self.read(*parts), parts[-1])
        for text in (self.read("README.md"),
                     self.read("docs", "sre", "30-appendix", "08-proactive-extensions.md"),
                     workshop.HEALTH_CHECK_TEMPLATE.read_text(encoding="utf-8")):
            self.assertIn("0 20 * * 0-4", text)
        self.assertIn("(../30-appendix/08-proactive-extensions.md)", self.read(*self.MODULE))

    def test_new_module_follows_the_documentation_standards(self):
        text = self.read(*self.MODULE)
        self.assertTrue(text.startswith("---\n"))
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        for key in ("title", "description", "ms.date", "ms.topic"):
            self.assertIn(key, frontmatter)
        fenced = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                if not fenced:
                    self.assertRegex(stripped, r"^```\w+$", f"fence without a language: {line!r}")
                fenced = not fenced
            elif not fenced:
                self.assertFalse(line.startswith("# "), "Pages must not contain an H1.")
        self.assertFalse(fenced)
        self.assertNotIn("\u2014", text)
        # "not just" makes a comparison rather than dismissing complexity.
        self.assertIsNone(re.search(
            r"\b(simply|easily|(?<!not )just|robust|seamless)\b", text, re.IGNORECASE))
        for expected in ("health-report-investigator", "orders-daily-health-report",
                         "proactive-health-check", "Orders Service Health"):
            self.assertIn(expected, text)


INCIDENT_GROUP = HEALTH_VALUES["SRE_AGENT_RESOURCE_ID"].split("/providers/")[0]
INCIDENT_VALUES = {
    **HEALTH_VALUES,
    "VM_RESOURCE_ID": INCIDENT_GROUP + "/providers/Microsoft.Compute/virtualMachines/vm-orders-test",
    "LOG_ANALYTICS_ID": INCIDENT_GROUP + "/providers/Microsoft.OperationalInsights/workspaces/law-orders-test",
    "LOG_ANALYTICS_CUSTOMER_ID": "00000000-0000-0000-0000-000000000002",
    "POSTGRESQL_FAULT_RULE_RESOURCE_ID": (
        INCIDENT_GROUP + "/providers/Microsoft.Network/networkSecurityGroups/"
        "nsg-orders-test/securityRules/PostgreSqlFaultInjection"),
}
INCIDENT_PLAN_PATH = EXTENDED + "incidentFilters/orders-incident-workflow"
INCIDENT_AGENT_PATH = EXTENDED + "agents/orders-alert-investigator"
INCIDENT_PATHS = [
    *(EXTENDED + "skills/" + name for name in workshop.INCIDENT_SKILLS),
    INCIDENT_AGENT_PATH, INCIDENT_PLAN_PATH,
]
SOURCE_REPOSITORY = "https://github.com/workshop-learner/orders"
INCIDENT_REPOSITORY = "https://github.com/workshop-learner/incidents"


def incident_original_plan():
    return {
        "name": workshop.SRE_RESPONSE_PLAN_NAME, "type": "IncidentFilter", "tags": [],
        "properties": {
            "incidentPlatform": "AzMonitor", "isEnabled": True,
            "priorities": ["Sev1", "Sev2"], "handlingAgent": "meta_agent",
            "agentMode": "Review", "titleContains": "", "titleContainsAll": [],
            "titleContainsAny": [], "titleNotContains": [],
            "mergeEnabled": True, "mergeWindowHours": 3,
            "maxAutomatedInvestigationAttempts": 3,
        },
    }


class FakeIncidentAgent(FakeSreAgent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.resources[RESPONSE_PLAN_PATH] = incident_original_plan()
        self.optional = {}
        self.versions = {}
        self.history = []
        self.fail_after_activation = False

    def request(self, endpoint, token, path, method="GET", body=None, *,
                if_match=None, with_etag=False):
        self.history.append((method, path, copy.deepcopy(body), if_match))
        if if_match is not None and if_match != f'"{self.versions.get(path, 0)}"':
            raise workshop.SreAgentRequestError("precondition failed", status=412)
        if method == "GET" and path == EXTENDED + "incidentFilters":
            self.calls.append((method, path))
            document = {"value": [
                copy.deepcopy(item) for key, item in self.resources.items()
                if key.startswith(path + "/")]}
        elif method == "GET" and path in self.optional:
            self.calls.append((method, path))
            document = copy.deepcopy(self.optional[path])
        else:
            document = copy.deepcopy(super().request(endpoint, token, path, method, body))
            if method in {"PUT", "DELETE"}:
                self.versions[path] = self.versions.get(path, 0) + 1
            if (self.fail_after_activation and path == INCIDENT_PLAN_PATH and method == "PUT"
                    and body["properties"]["isEnabled"]):
                self.fail_after_activation = False
                raise workshop.SreAgentRequestError("activation response lost", status=503, retryable=True)
        return (document, f'"{self.versions.get(path, 0)}"') if with_etag else document

    def connect_followups(self):
        self.optional.update({
            "/api/v2/repos": {"value": [
                {"name": "source", "properties": {"url": SOURCE_REPOSITORY}},
                {"name": "incidents", "properties": {"url": INCIDENT_REPOSITORY}},
            ]},
            "/api/v2/github/domains": {"values": ["github.com"]},
            "/api/v2/agent/settings/global": {"permissions": {
                "allow": ["RunAzCliReadCommands"],
                "ask": ["CreateGithubIssue", "SendOutlookEmail"],
                "deny": ["RunAzCliWriteCommands"],
            }},
            "/api/v2/connectorV2/mcpservers/office365": {"properties": {
                "connectors": [{"name": "office365", "connectionName": "office365"}]}},
            "/api/v2/connectorV2/connections/office365": {
                "properties": {"overallStatus": "Connected"}},
        })


class IncidentWorkflowTests(unittest.TestCase):
    @contextmanager
    def patched(self, fake=None, connectors=None, mutate_parent=None, mutate_alert=None):
        fake = fake or FakeIncidentAgent()

        def azure(*arguments, **kwargs):
            url = arguments[-1]
            if url.endswith("/DataConnectors?api-version=2025-05-01-preview"):
                return connectors or connector_response()
            if "/providers/Microsoft.Insights/metricAlerts/" in url:
                return {
                    "id": url.split("https://management.azure.com")[1].split("?")[0],
                    "name": "alert-orders-high-cpu",
                    "properties": {
                        "enabled": True, "severity": 2,
                        "scopes": [INCIDENT_VALUES["VM_RESOURCE_ID"]],
                        "description": "Orders API VM CPU utilization is sustained above the configured threshold.",
                    },
                }
            if "/providers/Microsoft.Insights/scheduledQueryRules/" in url:
                name = url.split("/scheduledQueryRules/")[1].split("?")[0]
                alert = {
                    "id": url.split("https://management.azure.com")[1].split("?")[0],
                    "name": name,
                    "properties": {
                        "enabled": True, "severity": 1,
                        "scopes": [INCIDENT_VALUES["LOG_ANALYTICS_ID"]],
                        "displayName": "Orders PostgreSQL dependency failures"
                        if name.endswith("connectivity") else "Orders API HTTP 5xx responses",
                    },
                }
                if mutate_alert:
                    mutate_alert(alert)
                return alert
            parent = {
                "id": INCIDENT_VALUES["SRE_AGENT_RESOURCE_ID"],
                "properties": {
                    "agentEndpoint": INCIDENT_VALUES["SRE_AGENT_ENDPOINT"],
                    "actionConfiguration": {"accessLevel": "Low", "mode": "Review"},
                    "incidentManagementConfiguration": {"type": "AzMonitor"},
                    "knowledgeGraphConfiguration": {"managedResources": [INCIDENT_GROUP]},
                },
            }
            if mutate_parent:
                mutate_parent(parent)
            return parent

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(workshop, "ROOT", Path(temporary)))
            self.azure = stack.enter_context(patch.object(workshop, "az", side_effect=azure))
            self.auth = stack.enter_context(patch.object(
                workshop, "sre_agent_token", return_value="secret-token", autospec=True))
            stack.enter_context(patch.object(workshop, "sre_agent_request", side_effect=fake.request))
            stack.enter_context(patch.object(workshop.time, "sleep"))
            stack.enter_context(patch.object(workshop, "progress"))
            yield fake, Path(temporary)

    def install(self, settings=None):
        return workshop.ensure_incident_workflow(
            INCIDENT_VALUES, settings, attempts=1, retry_seconds=0)

    def remove(self):
        return workshop.remove_incident_workflow(
            INCIDENT_VALUES, attempts=1, retry_seconds=0)

    def test_definition_composes_canonical_context_and_separate_skills(self):
        definition = workshop.load_incident_definition()
        self.assertEqual(definition["name"], "orders-incident-workflow")
        self.assertEqual(definition["agent"], "orders-alert-investigator")
        self.assertEqual([skill["name"] for skill in definition["skills"]], list(workshop.INCIDENT_SKILLS))
        content = definition["skills"][0]["content"]
        for expected in ("PostgreSqlFaultInjection", 'DependencyType == "PostgreSQL"',
                         "The VM is a single point of failure", "causal reasoning",
                         "orders-api-postgresql", "fault reset-postgresql"):
            self.assertIn(expected.lower(), content.lower())
        self.assertNotIn("APP_FAULT_ENABLED", content)
        self.assertEqual(set(definition["tools"]), workshop.INCIDENT_READ_TOOLS)
        self.assertEqual(set(definition["approvalTools"]), workshop.INCIDENT_APPROVAL_TOOLS)
        self.assertEqual(set(definition["writeTools"]), workshop.INCIDENT_WRITE_TOOLS)
        self.assertIn("--access Allow", definition["instructions"])
        _, agent = workshop.skill_agent_documents(definition)
        self.assertNotIn("agentType", agent["properties"])
        self.assertIs(agent["properties"]["enableVanillaMode"], False)

    def test_unsafe_definitions_fail_before_writes(self):
        cases = [
            lambda d: d["custom_agent"]["tools"]["read_only"].append("RunAzCliWriteCommands"),
            lambda d: d["custom_agent"]["tools"]["autonomous_write"].append("CreateFile"),
            lambda d: d["custom_agent"]["tools"]["ask_approval"].append("CreateFile"),
            lambda d: d["custom_agent"]["tools"]["autonomous_write"].clear() or
            d["custom_agent"]["tools"]["autonomous_write"].append("RunInTerminal"),
            lambda d: d["custom_agent"]["tools"]["deny"].remove("Terminal"),
            lambda d: d["trigger"].update(action_mode="Review"),
            lambda d: d["custom_agent"].update(action_mode="Review"),
            lambda d: d["custom_agent"].update(action_mode="Automatic"),
            lambda d: d["trigger"]["filters"].update(severities=["Sev1"]),
            lambda d: d["trigger"]["filters"].update(alert_rules=["flu"]),
            lambda d: d["trigger"]["filters"].update(merge_window_hours=4),
            lambda d: d["custom_agent"]["skills"][0].update(name="another-skill"),
            lambda d: d["custom_agent"]["skills"][0].update(source="../../outside.md"),
            lambda d: d["custom_agent"]["context_sources"].__setitem__(0, "../../outside.md"),
            lambda d: d.update(name="another-plan"),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "agent"
                shutil.copytree(workshop.AGENT_DEFINITIONS, root)
                template = root / "workflows" / "orders-incident-workflow.yaml"
                document = yaml.safe_load(template.read_text())
                mutate(document)
                template.write_text(yaml.safe_dump(document), encoding="utf-8")
                with patch.object(workshop, "AGENT_DEFINITIONS", root), \
                        self.assertRaises(workshop.DeploymentError):
                    workshop.load_incident_definition(template)

    def test_invalid_yaml_is_reported_as_a_deployment_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            template = Path(temporary) / "invalid.yaml"
            template.write_text("name: [unclosed", encoding="utf-8")
            with self.assertRaisesRegex(workshop.DeploymentError, "valid YAML"):
                workshop.load_incident_definition(template)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent"
            root.mkdir()
            skill = root / "SKILL.md"
            skill.write_text("---\nname: [unclosed\n---\nBody\n", encoding="utf-8")
            with patch.object(workshop, "AGENT_DEFINITIONS", root), \
                    self.assertRaisesRegex(workshop.DeploymentError, "frontmatter"):
                workshop.read_health_check_skill(skill, "orders-azure-monitor-rca")

    def test_install_stages_partition_then_enables_and_saves_nonsecret_evidence(self):
        with self.patched() as (fake, temporary):
            original = copy.deepcopy(fake.resources[RESPONSE_PLAN_PATH])
            fake.resources[HEALTH_TASK_PATH] = {"name": "unrelated-health-task"}
            result = self.install()
            state = workshop.incident_read_state(INCIDENT_VALUES)
            evidence = json.loads(
                (workshop.incident_state_path(INCIDENT_VALUES).parent
                 / workshop.INCIDENT_WORKFLOW_EVIDENCE).read_text())
            writes = [item for item in fake.history if item[0] == "PUT"]
            self.assertEqual([item[1] for item in writes], [
                *INCIDENT_PATHS, RESPONSE_PLAN_PATH, INCIDENT_PLAN_PATH])
            self.assertFalse(writes[4][2]["properties"]["isEnabled"])
            self.assertTrue(writes[-1][2]["properties"]["isEnabled"])
            self.assertIsNotNone(writes[5][3])
            self.assertTrue(result["ok"] and result["installed"] and result["matchesDefinition"])
            self.assertEqual(evidence, result)
            self.assertEqual(state["originalPlan"], original)
            self.assertEqual(state["phase"], "active")
            self.assertNotIn("secret-token", json.dumps(state))
            self.assertNotIn("secret-token", json.dumps(evidence))
            self.assertIn(HEALTH_TASK_PATH, fake.resources)
            self.assertTrue(fake.resources[RESPONSE_PLAN_PATH]["properties"]["isEnabled"])
            self.assertTrue(fake.resources[INCIDENT_PLAN_PATH]["properties"]["isEnabled"])
            for path in (RESPONSE_PLAN_PATH, INCIDENT_PLAN_PATH):
                self.assertEqual(fake.resources[path]["properties"]["priorities"], ["Sev1", "Sev2"])
            self.assertEqual(fake.resources[RESPONSE_PLAN_PATH]["properties"]["agentMode"], "Review")
            self.assertEqual(fake.resources[INCIDENT_PLAN_PATH]["properties"]["agentMode"], "Autonomous")
            self.assertEqual(result["routing"]["mode"], "Autonomous")
            self.assertEqual(len(result["routing"]["includedTitles"]), 2)
            self.assertEqual(result["routing"]["workflowTitleContains"], "postgresql")
            self.assertEqual(result["routing"]["originalTitleContains"], "alert-orders-h")
            tools = fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"]
            self.assertEqual(set(tools), workshop.HEALTH_CHECK_TOOLS | workshop.INCIDENT_WRITE_TOOLS
                             | set(workshop.HEALTH_CONNECTOR_TOOLS.values()))
            self.assertFalse(result["followups"]["github"]["enabled"])
            self.assertFalse(result["followups"]["email"]["enabled"])
            self.auth.assert_called_with(INCIDENT_VALUES)

    def test_real_name_and_display_title_fixtures_are_complementary(self):
        with self.patched() as (fake, _):
            result = self.install()
            baseline = fake.resources[RESPONSE_PLAN_PATH]["properties"]
            module = fake.resources[INCIDENT_PLAN_PATH]["properties"]
            for title in result["routing"]["includedTitles"]:
                for shaped in (title, "[Fired] " + title, title.upper()):
                    self.assertFalse(workshop.incident_title_matches(baseline, shaped))
                    self.assertTrue(workshop.incident_title_matches(module, shaped))
            self.assertTrue(workshop.incident_title_matches(baseline, "alert-orders-high-cpu"))
            self.assertFalse(workshop.incident_title_matches(module, "alert-orders-high-cpu"))
            self.assertTrue(workshop.incident_title_matches(baseline, "alert-orders-http-5xx"))
            self.assertFalse(workshop.incident_title_matches(module, "alert-orders-http-5xx"))
            self.assertFalse(workshop.incident_title_matches(baseline, "another alert"))
            self.assertFalse(workshop.incident_title_matches(module, "another alert"))

    def test_install_is_idempotent_and_never_resnapshots_partitioned_filters(self):
        with self.patched() as (fake, _):
            self.install()
            saved = workshop.incident_read_state(INCIDENT_VALUES)
            before = copy.deepcopy(fake.resources)
            writes = len(fake.methods("PUT"))
            again = self.install()
            self.assertTrue(again["matchesDefinition"])
            self.assertEqual(len(fake.methods("PUT")), writes)
            self.assertEqual(fake.resources, before)
            self.assertEqual(workshop.incident_read_state(INCIDENT_VALUES), saved)

    def test_explicit_followups_require_connected_bindings_and_exact_ask_policies(self):
        fake = FakeIncidentAgent()
        fake.connect_followups()
        settings = workshop.incident_settings(
            SOURCE_REPOSITORY, INCIDENT_REPOSITORY, "office365", ["operator@example.invalid"])
        with self.patched(fake):
            result = self.install(settings)
            tools = fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"]
            for expected in ("ReadFile", "FetchGithubIssues", "CreateGithubIssue",
                             "ListOutlookEmails", "SendOutlookEmail"):
                self.assertIn(expected, tools)
            self.assertTrue(all(item["enabled"] for item in result["followups"].values()))
            text = fake.resources[INCIDENT_AGENT_PATH]["properties"]["instructions"]
            self.assertIn(SOURCE_REPOSITORY, text)
            self.assertIn(INCIDENT_REPOSITORY, text)
            self.assertIn("operator@example.invalid", text)
            self.assertIn("RunAzCliWriteCommands", tools)
            self.assertNotIn("RunInTerminal", tools)
            self.assertEqual(fake.resources[INCIDENT_AGENT_PATH]["properties"]["mcpTools"], [])
            self.assertEqual(fake.resources[INCIDENT_AGENT_PATH]["properties"]["connectors"], [])
            self.install()

    def test_missing_or_ambiguous_approval_policies_keep_writes_unattached(self):
        for category, value in (
            ("ask", []), ("ask", ["*"]), ("allow", ["*"]),
            ("allow", ["CreateGithubIssue", "SendOutlookEmail"]), ("deny", ["*"]),
        ):
            with self.subTest(category=category, value=value):
                fake = FakeIncidentAgent()
                fake.connect_followups()
                fake.optional["/api/v2/agent/settings/global"]["permissions"][category] = value
                settings = workshop.incident_settings(
                    github_repository=INCIDENT_REPOSITORY,
                    outlook_connector="office365", email_to=["operator@example.invalid"])
                with self.patched(fake):
                    result = self.install(settings)
                    self.assertTrue(result["matchesDefinition"])
                    self.assertFalse(result["followups"]["github"]["enabled"])
                    self.assertFalse(result["followups"]["email"]["enabled"])
                    tools = fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"]
                    self.assertNotIn("CreateGithubIssue", tools)
                    self.assertNotIn("SendOutlookEmail", tools)

    def test_missing_connections_leave_explicit_drafts_without_blocking_core(self):
        settings = workshop.incident_settings(
            SOURCE_REPOSITORY, INCIDENT_REPOSITORY, "office365", ["operator@example.invalid"])
        with self.patched() as (fake, _):
            result = self.install(settings)
            self.assertTrue(result["installed"])
            self.assertTrue(all(not item["enabled"] and item["reason"]
                                for item in result["followups"].values()))
            tools = fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"]
            self.assertNotIn("ReadFile", tools)
            self.assertNotIn("CreateGithubIssue", tools)
            self.assertNotIn("SendOutlookEmail", tools)

    def test_unauthenticated_or_multiple_outlook_bindings_do_not_grant_send(self):
        for status in ("Unauthenticated", "Error", "Unknown", None):
            with self.subTest(status=status):
                fake = FakeIncidentAgent()
                fake.connect_followups()
                fake.optional["/api/v2/connectorV2/connections/office365"]["properties"]["overallStatus"] = status
                with self.patched(fake):
                    result = self.install(workshop.incident_settings(
                        outlook_connector="office365", email_to=["operator@example.invalid"]))
                    self.assertFalse(result["followups"]["email"]["enabled"])
                    self.assertNotIn("SendOutlookEmail", fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"])
        fake = FakeIncidentAgent()
        fake.connect_followups()
        fake.optional["/api/v2/connectorV2/mcpservers/office365"]["properties"]["connectors"].append(
            {"name": "office365", "connectionName": "other-mailbox"})
        with self.patched(fake):
            self.assertFalse(self.install(workshop.incident_settings(
                outlook_connector="office365", email_to=["operator@example.invalid"]))["followups"]["email"]["enabled"])

    def test_unsafe_destination_options_are_rejected(self):
        for repository in ("http://github.com/owner/repo", "https://example.com/owner/repo",
                           "https://github.com/user/repo/issues", "https://github.com/user/..",
                           "https://github.com/user/repo.git", "https://user@github.com/user/repo"):
            with self.subTest(repository=repository), self.assertRaises(workshop.DeploymentError):
                workshop.incident_settings(github_repository=repository)
        for recipient in ("user@example.invalid\r\nBcc: other@example.invalid",
                          "Person <user@example.invalid>", "a@example.invalid,b@example.invalid"):
            with self.subTest(recipient=recipient), self.assertRaises(workshop.DeploymentError):
                workshop.incident_settings(outlook_connector="office365", email_to=[recipient])
        with self.assertRaises(workshop.DeploymentError):
            workshop.incident_settings(outlook_connector="office365")
        with self.assertRaises(workshop.DeploymentError):
            workshop.incident_settings(email_to=["user@example.invalid"])
        with self.assertRaises(workshop.DeploymentError):
            workshop.incident_settings(outlook_connector="../office365", email_to=["user@example.invalid"])

    def test_prerequisite_failures_make_no_configuration_write(self):
        mutations = [
            lambda p: p["properties"]["actionConfiguration"].update(accessLevel="High"),
            lambda p: p["properties"]["actionConfiguration"].update(mode="Automatic"),
            lambda p: p["properties"]["incidentManagementConfiguration"].update(type="Other"),
            lambda p: p["properties"]["knowledgeGraphConfiguration"].update(managedResources=[]),
            lambda p: p["properties"]["knowledgeGraphConfiguration"].update(
                managedResources=[INCIDENT_GROUP, "/another-group"]),
            lambda p: p["properties"].update(agentEndpoint="https://another.australiaeast.azuresre.ai"),
            lambda p: p["properties"].update(actionConfiguration=None),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.patched(mutate_parent=mutate) as (fake, _):
                with self.assertRaises(workshop.DeploymentError):
                    self.install()
                self.assertFalse(fake.methods("PUT"))
        with self.patched(connectors=connector_response(log_analytics="Failed")) as (fake, _):
            with self.assertRaisesRegex(workshop.DeploymentError, "log-analytics"):
                self.install()
            self.assertFalse(fake.methods("PUT"))

    def test_changed_or_disabled_alerts_fail_before_writes(self):
        for mutate in (
            lambda a: a["properties"].update(enabled=False),
            lambda a: a["properties"].update(severity=2),
            lambda a: a["properties"].update(severity=True),
            lambda a: a["properties"].update(displayName="CPU\nand PostgreSQL"),
            lambda a: a["properties"].update(displayName="cpu"),
            lambda a: a["properties"].update(displayName="Orders API"),
            lambda a: a["properties"].update(scopes=["/another-workspace"]),
        ):
            with self.subTest(mutate=mutate), self.patched(mutate_alert=mutate) as (fake, _):
                with self.assertRaises(workshop.DeploymentError):
                    self.install()
                self.assertFalse(fake.methods("PUT"))

    def test_resource_scope_id_comparisons_follow_arm_case_insensitivity(self):
        with self.patched(mutate_alert=lambda a: a["properties"].update(
                scopes=[INCIDENT_VALUES["LOG_ANALYTICS_ID"].upper()])):
            self.assertTrue(self.install()["matchesDefinition"])

    def test_unowned_components_and_other_overlapping_plans_are_never_replaced(self):
        for path in INCIDENT_PATHS:
            fake = FakeIncidentAgent()
            fake.resources[path] = {"name": path.rsplit("/", 1)[1], "properties": {"owner": "another"}}
            with self.subTest(path=path), self.patched(fake):
                with self.assertRaises(workshop.DeploymentError):
                    self.install()
                self.assertFalse(fake.methods("PUT"))
                self.assertIn(path, fake.resources)
        fake = FakeIncidentAgent()
        other = incident_original_plan()
        other["name"] = "another-active-plan"
        fake.resources[EXTENDED + "incidentFilters/another-active-plan"] = other
        with self.patched(fake):
            with self.assertRaisesRegex(workshop.DeploymentError, "another enabled"):
                self.install()
            self.assertFalse(fake.methods("PUT"))

    def test_dropped_scalar_title_aborts_before_activation_and_restores_original(self):
        fake = FakeIncidentAgent(on_put={
            RESPONSE_PLAN_PATH: lambda body: body["properties"].update(titleContains="")})
        with self.patched(fake):
            original = copy.deepcopy(fake.resources[RESPONSE_PLAN_PATH])
            with self.assertRaisesRegex(workshop.DeploymentError, "read-back"):
                self.install()
            self.assertEqual(fake.resources, {RESPONSE_PLAN_PATH: original})
            activations = [item for item in fake.history if item[0] == "PUT"
                           and item[1] == INCIDENT_PLAN_PATH and item[2]["properties"]["isEnabled"]]
            self.assertFalse(activations)
            self.assertFalse(workshop.incident_state_path(INCIDENT_VALUES).exists())

    def test_lost_activation_response_is_reconciled_by_disable_and_rollback_not_blind_retry(self):
        fake = FakeIncidentAgent()
        fake.fail_after_activation = True
        with self.patched(fake):
            original = copy.deepcopy(fake.resources[RESPONSE_PLAN_PATH])
            with self.assertRaisesRegex(workshop.DeploymentError, "activation response lost"):
                self.install()
            self.assertEqual(fake.resources, {RESPONSE_PLAN_PATH: original})
            activations = [item for item in fake.history if item[0] == "PUT"
                           and item[1] == INCIDENT_PLAN_PATH and item[2]["properties"]["isEnabled"]]
            self.assertEqual(len(activations), 1)

    def test_incomplete_disabled_plan_restores_original_before_refusing_changed_component_deletion(self):
        with self.patched() as (fake, _):
            original = copy.deepcopy(fake.resources[RESPONSE_PLAN_PATH])
            self.install()
            fake.resources[INCIDENT_PLAN_PATH]["properties"].update(
                isEnabled=False, titleContainsAny=["operator-edited-title"])
            with self.assertRaisesRegex(workshop.DeploymentError, "refusing to delete"):
                self.remove()
            self.assertEqual(fake.resources[RESPONSE_PLAN_PATH], original)
            self.assertFalse(fake.resources[INCIDENT_PLAN_PATH]["properties"]["isEnabled"])
            self.assertEqual(workshop.incident_read_state(INCIDENT_VALUES)["phase"], "removing")

    def test_status_reads_only_and_detects_live_filter_tool_and_definition_drift(self):
        with self.patched() as (fake, _):
            self.install()
            writes = len(fake.methods("PUT"))
            status = workshop.incident_workflow_status(INCIDENT_VALUES)
            self.assertTrue(status["matchesDefinition"])
            self.assertEqual(len(fake.methods("PUT")), writes)
            fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"].append("RunAzCliWriteCommands")
            status = workshop.incident_workflow_status(INCIDENT_VALUES)
            self.assertFalse(status["matchesDefinition"])
            self.assertTrue(any("orders-alert-investigator" in item for item in status["problems"]))
            fake.resources[RESPONSE_PLAN_PATH]["properties"]["titleContains"] = ""
            self.assertFalse(workshop.incident_workflow_status(INCIDENT_VALUES)["matchesDefinition"])
            self.assertEqual(len(fake.methods("PUT")), writes)
            with self.assertRaises(workshop.DeploymentError):
                self.install()

    def test_changed_notification_policy_is_visible_without_success_shaped_fallback(self):
        fake = FakeIncidentAgent()
        fake.connect_followups()
        with self.patched(fake):
            self.install(workshop.incident_settings(github_repository=INCIDENT_REPOSITORY))
            fake.optional["/api/v2/agent/settings/global"]["permissions"]["ask"] = []
            report = workshop.incident_workflow_status(INCIDENT_VALUES)
            self.assertFalse(report["matchesDefinition"])
            self.assertFalse(report["followups"]["github"]["enabled"])
            self.assertTrue(report["problems"])

    def test_component_checks_reject_malformed_tools_and_nonboolean_enabled_state(self):
        with self.patched() as (fake, _):
            self.install()
            expected = workshop.incident_read_state(INCIDENT_VALUES)["documents"]
            fake.resources[INCIDENT_PLAN_PATH]["properties"]["isEnabled"] = 1
            self.assertFalse(workshop.incident_component_matches(
                fake.resources[INCIDENT_PLAN_PATH], expected[INCIDENT_PLAN_PATH]))
            fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"] = [{}]
            self.assertFalse(workshop.incident_component_matches(
                fake.resources[INCIDENT_AGENT_PATH], expected[INCIDENT_AGENT_PATH]))

    def test_live_skill_dto_uses_envelope_name_without_redundant_property_name(self):
        fake = FakeIncidentAgent(on_put={
            EXTENDED + "skills/" + name: lambda body: body["properties"].pop("name")
            for name in workshop.INCIDENT_SKILLS})
        with self.patched(fake):
            self.assertTrue(self.install()["matchesDefinition"])
            self.assertTrue(self.remove()["ok"])
        definition = workshop.load_incident_definition()
        skills, _ = workshop.skill_agent_documents(definition)
        actual = copy.deepcopy(skills[0])
        actual["properties"]["name"] = "a-different-skill"
        self.assertFalse(workshop.incident_component_matches(actual, skills[0]))

    def test_live_plan_dto_null_tags_and_server_timestamps_are_not_configuration_drift(self):
        def returned_plan(body):
            body["tags"] = None
            body["properties"].update(
                createdAt="2026-10-13T00:00:00Z",
                updatedAt="2026-10-13T00:01:00Z",
                configurationWarning="Server-calculated diagnostic")

        fake = FakeIncidentAgent(on_put={
            RESPONSE_PLAN_PATH: returned_plan, INCIDENT_PLAN_PATH: returned_plan})
        returned_plan(fake.resources[RESPONSE_PLAN_PATH])
        original = workshop.incident_plan_document(fake.resources[RESPONSE_PLAN_PATH])
        with self.patched(fake):
            self.assertTrue(self.install()["matchesDefinition"])
            state = workshop.incident_read_state(INCIDENT_VALUES)
            self.assertFalse(
                set(state["originalPlan"]["properties"]) & workshop.INCIDENT_PLAN_READ_ONLY_PROPERTIES)
            fake.resources[RESPONSE_PLAN_PATH]["properties"]["updatedAt"] = "2026-10-13T00:02:00Z"
            self.assertTrue(workshop.incident_workflow_status(INCIDENT_VALUES)["matchesDefinition"])
            self.assertTrue(self.remove()["ok"])
            self.assertEqual(workshop.incident_plan_document(fake.resources[RESPONSE_PLAN_PATH]), original)

    def test_status_reports_absence_or_orphans_without_writes(self):
        with self.patched() as (fake, _):
            result = workshop.incident_workflow_status(INCIDENT_VALUES)
            self.assertFalse(result["installed"])
            self.assertEqual(result["phase"], "absent")
            self.assertFalse(result["unownedComponents"])
            self.assertFalse(fake.methods("PUT"))
            fake.resources[INCIDENT_AGENT_PATH] = {"name": "orders-alert-investigator"}
            self.assertEqual(workshop.incident_workflow_status(INCIDENT_VALUES)["unownedComponents"],
                             ["orders-alert-investigator"])
            with self.assertRaises(workshop.DeploymentError):
                self.remove()
            self.assertFalse(fake.methods("DELETE"))

    def test_removal_restores_exact_original_filters_and_keeps_shared_resources(self):
        with self.patched() as (fake, _):
            original = fake.resources[RESPONSE_PLAN_PATH]
            original["properties"]["titleNotContains"] = ["unrelated incident"]
            original["tags"] = ["operator-owned-tag"]
            original["properties"]["owningTeamId"] = "existing-team"
            expected = copy.deepcopy(original)
            self.install()
            fake.optional["/api/v2/repos"] = {"value": []}
            fake.resources[HEALTH_TASK_PATH] = {"name": "unrelated-health-task"}
            start = len(fake.history)
            result = self.remove()
            self.assertTrue(result["ok"])
            self.assertEqual(fake.resources[RESPONSE_PLAN_PATH], expected)
            self.assertEqual(set(fake.resources), {RESPONSE_PLAN_PATH, HEALTH_TASK_PATH})
            writes = [item for item in fake.history[start:] if item[0] == "PUT"]
            self.assertEqual([item[1] for item in writes], [INCIDENT_PLAN_PATH, RESPONSE_PLAN_PATH])
            self.assertFalse(writes[0][2]["properties"]["isEnabled"])
            self.assertIsNotNone(writes[1][3])
            self.assertFalse(workshop.incident_state_path(INCIDENT_VALUES).exists())
            self.assertEqual(self.remove()["removed"], [])
            self.assertIn("/api/v2/repos", fake.optional)

    def test_manual_plan_edits_stop_restoration_instead_of_overwriting(self):
        with self.patched() as (fake, _):
            self.install()
            fake.resources[RESPONSE_PLAN_PATH]["properties"]["owningTeamId"] = "changed-team"
            with self.assertRaisesRegex(workshop.DeploymentError, "Concurrent"):
                self.remove()
            self.assertEqual(fake.resources[RESPONSE_PLAN_PATH]["properties"]["owningTeamId"], "changed-team")
            self.assertFalse(fake.resources[INCIDENT_PLAN_PATH]["properties"]["isEnabled"])
            self.assertEqual(workshop.incident_read_state(INCIDENT_VALUES)["phase"], "removing")

    def test_changed_destinations_require_removal(self):
        with self.patched() as (fake, _):
            self.install()
            writes = len(fake.methods("PUT"))
            with self.assertRaisesRegex(workshop.DeploymentError, "changing"):
                self.install(workshop.incident_settings(github_repository=INCIDENT_REPOSITORY))
            self.assertEqual(len(fake.methods("PUT")), writes)

    def test_repeated_provisioning_preserves_active_partition_without_config_writes(self):
        with self.patched() as (fake, _):
            self.install()
            before = copy.deepcopy(fake.resources)
            writes = len(fake.methods("PUT"))
            result = workshop.ensure_sre_response_plan(INCIDENT_VALUES, attempts=1)
            self.assertEqual(result["incidentWorkflow"], "orders-incident-workflow")
            self.assertEqual(fake.resources, before)
            self.assertEqual(len(fake.methods("PUT")), writes)

    def test_provisioning_refuses_to_restore_catchall_after_drift_or_lost_state(self):
        for lost_state in (False, True):
            with self.subTest(lost_state=lost_state), self.patched() as (fake, _):
                self.install()
                if lost_state:
                    workshop.incident_state_path(INCIDENT_VALUES).unlink()
                else:
                    fake.resources[INCIDENT_AGENT_PATH]["properties"]["tools"].append("Terminal")
                writes = len(fake.methods("PUT"))
                with self.assertRaisesRegex(workshop.DeploymentError, "overlapping catch-all"):
                    workshop.ensure_sre_response_plan(INCIDENT_VALUES, attempts=1)
                self.assertEqual(len(fake.methods("PUT")), writes)
                self.assertEqual(fake.resources[RESPONSE_PLAN_PATH]["properties"]["titleContains"], "alert-orders-h")

    def test_state_is_bound_to_environment_and_rejects_tampered_partitions(self):
        for key, value in (("agentResourceId", "/another-agent"), ("version", 99)):
            with self.subTest(key=key), self.patched():
                self.install()
                state = workshop.incident_read_state(INCIDENT_VALUES)
                state[key] = value
                workshop.save_incident_state(INCIDENT_VALUES, state)
                with self.assertRaises(workshop.DeploymentError):
                    workshop.incident_read_state(INCIDENT_VALUES)
        with self.patched():
            self.install()
            state = workshop.incident_read_state(INCIDENT_VALUES)
            state["partitionedPlan"]["properties"]["priorities"] = ["Sev1"]
            workshop.save_incident_state(INCIDENT_VALUES, state)
            with self.assertRaises(workshop.DeploymentError):
                self.remove()

    def test_cli_destinations_are_install_only_and_use_selected_values(self):
        with patch.object(workshop, "environment", return_value=INCIDENT_VALUES), \
                patch.object(workshop, "incident_workflow", return_value={"ok": True}) as handler, \
                patch.object(sys, "argv", ["workshop.py", "incident-workflow", "install",
                                          "--github-repository", INCIDENT_REPOSITORY]), \
                redirect_stdout(io.StringIO()):
            workshop.main()
        handler.assert_called_once_with(
            INCIDENT_VALUES, "install", workshop.incident_settings(github_repository=INCIDENT_REPOSITORY))
        with self.assertRaises(workshop.DeploymentError):
            workshop.incident_workflow(INCIDENT_VALUES, "status",
                                       workshop.incident_settings(github_repository=INCIDENT_REPOSITORY))
        with self.assertRaises(workshop.DeploymentError):
            workshop.incident_workflow(INCIDENT_VALUES, "bogus")
        with patch.object(sys, "argv", ["workshop.py", "validate",
                                      "--github-repository", INCIDENT_REPOSITORY]), \
                self.assertRaises(workshop.DeploymentError):
            workshop.main()

    def test_data_plane_metadata_uses_conditional_etags_without_changing_normal_calls(self):
        import email.message
        headers = email.message.Message()
        headers["ETag"] = '"version-1"'
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = headers
        response.read.return_value = b'{"name":"test"}'
        opener = unittest.mock.MagicMock()
        opener.open.return_value = response
        with patch.object(workshop.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(workshop.sre_agent_request(
                INCIDENT_VALUES["SRE_AGENT_ENDPOINT"], "token", "/test"), {"name": "test"})
            self.assertEqual(workshop.sre_agent_request(
                INCIDENT_VALUES["SRE_AGENT_ENDPOINT"], "token", "/test", with_etag=True),
                ({"name": "test"}, '"version-1"'))
            workshop.sre_agent_request(
                INCIDENT_VALUES["SRE_AGENT_ENDPOINT"], "token", "/test",
                "PUT", {}, if_match='"version-1"')
            self.assertEqual(opener.open.call_args.args[0].get_header("If-match"), '"version-1"')
            headers.replace_header("ETag", 'W/"weak"')
            with self.assertRaises(workshop.DeploymentError):
                workshop.sre_agent_request(
                    INCIDENT_VALUES["SRE_AGENT_ENDPOINT"], "token", "/test", with_etag=True)
            del headers["ETag"]
            self.assertEqual(workshop.sre_agent_request(
                INCIDENT_VALUES["SRE_AGENT_ENDPOINT"], "token", "/test", with_etag=True),
                ({"name": "test"}, None))
        with self.assertRaises(workshop.DeploymentError):
            workshop.sre_agent_request(
                INCIDENT_VALUES["SRE_AGENT_ENDPOINT"], "token", "/test", "PUT", {}, if_match="*")


class IncidentWorkflowDocumentationTests(unittest.TestCase):
    REPO = HERE.parent

    def read(self, *parts):
        return self.REPO.joinpath(*parts).read_text(encoding="utf-8")

    def test_navigation_preserves_the_five_participant_modules(self):
        navigation = self.read("mkdocs.yml")
        self.assertEqual(
            re.findall(r"^  - Module (\d{2}) - [^:\n]+: (\S+)$", navigation, re.MULTILINE),
            [
                ("01", "sre/01-deploy-and-validate/index.md"),
                ("02", "sre/03-incident-high-cpu/index.md"),
                ("03", "sre/04-incident-postgresql/index.md"),
                ("04", "sre/05-scheduled-health-check/index.md"),
                ("05", "sre/06-cleanup/index.md"),
            ],
        )
        self.assertNotIn("sre/incident-workflow/index.md", navigation)
        self.assertFalse((self.REPO / "docs" / "sre" / "incident-workflow" / "index.md").exists())

    def test_incident_workflow_evidence_remains_registered(self):
        self.assertIn(workshop.INCIDENT_WORKFLOW_STATE, workshop.FRESH_ENVIRONMENT_EVIDENCE)
        self.assertIn(workshop.INCIDENT_WORKFLOW_EVIDENCE, workshop.FRESH_ENVIRONMENT_EVIDENCE)

    def test_definitions_follow_existing_documentation_standards(self):
        for path in (workshop.INCIDENT_WORKFLOW_TEMPLATE,
                     *(workshop.AGENT_DEFINITIONS / "skills" / name / "SKILL.md"
                       for name in workshop.INCIDENT_SKILLS)):
            self.assertNotIn(b"\r", path.read_bytes(), path.name)
            self.assertNotIn("\u2014", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
