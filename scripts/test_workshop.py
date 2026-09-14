"""Offline regression tests for the deployment hooks; uses the Python standard library."""

import copy
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml


SPEC = importlib.util.spec_from_file_location("workshop", Path(__file__).with_name("workshop.py"))
workshop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workshop)


class ManifestTests(unittest.TestCase):
    def test_checked_in_configuration(self):
        filters, knowledge = workshop.load_config()
        self.assertEqual(filters[0]["spec"]["incidentPlatform"], "AzMonitor")
        self.assertEqual(len(knowledge["instructions"]), 2)
        self.assertEqual(len(knowledge["documents"]), 5)
        self.assertTrue(all(item["content"] for item in knowledge["documents"]))

    def test_duplicate_yaml_keys_rejected(self):
        with self.assertRaises(workshop.DeploymentError):
            yaml.load("version: 1\nversion: 2", Loader=workshop.UniqueKeyLoader)

    def test_unsafe_and_empty_sources_rejected(self):
        for source in ("", "../README.md", "/etc/passwd", "instructions/missing.md"):
            with self.subTest(source=source), self.assertRaises(workshop.DeploymentError):
                workshop.read_source(source)

    def test_invalid_manifest_fields(self):
        original_filter = yaml.safe_load((workshop.AGENT / "incident-filters.yaml").read_text())
        original_knowledge = yaml.safe_load((workshop.AGENT / "knowledge.yaml").read_text())
        for key, value in (
            ("isEnabled", "false"), ("priorities", ["Sev1", "Sev1"]),
            ("agentMode", "Autonomous"), ("incidentPlatform", "AzureMonitor"),
            ("maxAutomatedInvestigationAttempts", True), ("unknown", "ignored"),
        ):
            manifest = copy.deepcopy(original_filter)
            manifest["filters"][0]["spec"][key] = value
            with self.subTest(key=key), patch.object(workshop.yaml, "load", side_effect=[manifest, original_knowledge]):
                with self.assertRaises(workshop.DeploymentError):
                    workshop.load_config()

    def test_false_flag_is_preserved(self):
        manifest = yaml.safe_load((workshop.AGENT / "incident-filters.yaml").read_text())
        knowledge = yaml.safe_load((workshop.AGENT / "knowledge.yaml").read_text())
        manifest["filters"][0]["spec"]["isEnabled"] = False
        with patch.object(workshop.yaml, "load", side_effect=[manifest, knowledge]):
            filters, _ = workshop.load_config()
        self.assertIs(filters[0]["spec"]["isEnabled"], False)


class ExportTests(unittest.TestCase):
    def test_prepare_scrubs_legacy_secrets_without_generating_replacements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env_file = root / ".azure/test/.env"
            env_file.parent.mkdir(parents=True)
            env_file.write_text('AZURE_ENV_NAME="test"\nSQL_ADMIN_PASSWORD="obsolete-value"\nFAULT_TOKEN="obsolete-value"\n')
            config_file = env_file.with_name("config.json")
            config_file.write_text(json.dumps({"infra": {"parameters": {"sqlAdminPassword": "obsolete-value", "location": "eastus2"}}}))
            values = {"AZURE_ENV_NAME": "test", "AZURE_LOCATION": "eastus2", "SQL_ADMIN_PASSWORD": "obsolete-value"}
            claims = base64.urlsafe_b64encode(json.dumps({
                "oid": "00000000-0000-0000-0000-000000000001",
                "tid": "00000000-0000-0000-0000-000000000002",
            }).encode()).decode()
            token = "header." + claims + ".signature"
            responses = [{"accessToken": token}, ["East US 2"]]
            with patch.object(workshop, "ROOT", root), patch.object(workshop, "environment", return_value=values), \
                    patch.object(workshop, "az", side_effect=responses), patch.object(workshop, "cli", return_value=json.dumps({"token": token})) as cli, \
                    patch.object(workshop, "register_providers"), patch.dict(os.environ):
                workshop.prepare()
            self.assertNotIn("obsolete-value", env_file.read_text())
            self.assertNotIn("obsolete-value", config_file.read_text())
            self.assertEqual(json.loads(config_file.read_text())["infra"]["parameters"], {"location": "eastus2"})
            self.assertNotIn("obsolete-value", (root / ".workshop/workshop.env").read_text())
            self.assertNotIn("SQL_ADMIN_PASSWORD", str(cli.call_args_list))
            self.assertNotIn("FAULT_TOKEN", str(cli.call_args_list))

    def test_parameters_use_azd_principals_before_preprovision_runs(self):
        parameters = json.loads((workshop.ROOT / "infra/azd/main.parameters.json").read_text())["parameters"]
        self.assertEqual(parameters["deployerPrincipalId"]["value"], "${AZURE_PRINCIPAL_ID}")
        self.assertEqual(parameters["deployerPrincipalType"]["value"], "${AZURE_PRINCIPAL_TYPE}")

    def test_outputs_are_allowlisted_and_shell_quoted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {"RESOURCE_GROUP": "rg-'$(echo unsafe)", "SQL_ADMIN_PASSWORD": "never-export",
                      "FAULT_TOKEN": "never-export", "UNEXPECTED_SECRET": "never-export"}
            with patch.object(workshop, "ROOT", root):
                workshop.export_values(values)
            bash = (root / ".workshop/workshop.env").read_text()
            powershell = (root / ".workshop/workshop.ps1").read_text()
            self.assertNotIn("never-export", bash + powershell)
            self.assertIn("$env:RESOURCE_GROUP = 'rg-''$(echo unsafe)'", powershell)
            self.assertIn("export RESOURCE_GROUP=", bash)
            if os.name != "nt":
                self.assertEqual((root / ".workshop/workshop.env").stat().st_mode & 0o777, 0o600)

    def test_cli_errors_do_not_include_stdout_stderr(self):
        response = subprocess.CompletedProcess([], 1, stdout="sensitive-output", stderr="sensitive-error")
        with patch.object(workshop.shutil, "which", return_value="/usr/bin/az"), patch.object(workshop.subprocess, "run", return_value=response):
            with self.assertRaises(workshop.DeploymentError) as error:
                workshop.az("account", "show")
        self.assertNotIn("sensitive", str(error.exception))

    def test_credentials_are_sent_only_to_expected_endpoints(self):
        for url in ("http://agent.azuresre.ai", "https://azuresre.ai.attacker.example",
                    "https://" + "user@" + "agent.azuresre.ai", "https://agent.azuresre.ai:444",
                    "https://agent.azuresre.ai?redirect=elsewhere"):
            with self.subTest(url=url), self.assertRaises(workshop.DeploymentError):
                workshop.AgentClient(url)
        client = workshop.AgentClient("https://agent.eastus2.azuresre.ai")
        with patch.object(workshop, "az", return_value={"accessToken": "test-placeholder"}), patch.object(workshop, "http") as send:
            client.request("GET", "/api/v2/extendedAgent/commonprompts")
        self.assertEqual(send.call_args.args[2]["Authorization"], "Bearer " + "test-placeholder")

    def test_redirects_are_refused(self):
        with self.assertRaises(workshop.DeploymentError):
            workshop.NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.example")

    def test_fault_credential_is_only_used_in_memory(self):
        values = {"ORDERS_API_FQDN": "orders.example.azurecontainerapps.io", "KEY_VAULT_NAME": "kv-workshop"}
        output = io.StringIO()
        with patch.object(workshop, "environment", return_value=values), \
                patch.object(workshop, "az", return_value="a" * 64) as cli, \
                patch.object(workshop, "http", return_value={"status": "healthy"}) as http, \
                patch.object(workshop.sys, "stdout", output):
            workshop.fault("status", [])
        self.assertEqual(http.call_args.args[2]["X-Fault-Token"], "a" * 64)
        self.assertNotIn("a" * 64, output.getvalue())
        self.assertNotIn("a" * 64, str(cli.call_args_list))


class FakeAgent:
    def __init__(self):
        self.extended = {"commonprompts": {}, "incidentFilters": {}}
        self.files = {"unrelated.md": {"filename": "unrelated.md", "isIndexed": True}}
        self.uploads = []
        self.calls = []

    def request(self, method, path, body=None, content_type=None):
        self.calls.append((method, path))
        if path.startswith("/api/v2/extendedAgent/"):
            parts = path.split("/")
            route = parts[4]
            entries = self.extended[route]
            if method == "GET":
                return {"value": copy.deepcopy(list(entries.values()))}
            identifier = parts[5]
            if method == "PUT":
                entries[identifier] = copy.deepcopy(body)
            elif method == "DELETE":
                del entries[identifier]
            return None
        if path == "/api/v1/AgentMemory/files":
            return {"files": list(self.files.values())}
        if method == "DELETE":
            self.files.pop(path.rsplit("/", 1)[-1], None)
            return None
        if method == "POST" and "/AgentMemory/upload?" in path:
            filename = body.split(b'filename="')[1].split(b'"')[0].decode()
            if filename in self.files:
                raise AssertionError("Duplicate upload")
            self.files[filename] = {"filename": filename, "isIndexed": True}
            self.uploads.append(body)
            return None
        raise AssertionError(f"Unexpected API {method} {path}")


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeAgent()

    def test_two_runs_update_filters_and_prompts_without_duplicates(self):
        filters, knowledge = workshop.load_config()
        desired = [{"name": item["metadata"]["name"], "properties": item["spec"]} for item in filters]
        prompts = [{"name": item["name"], "properties": {"prompt": item["content"]}} for item in knowledge["instructions"]]
        for _ in range(2):
            workshop.reconcile_extended(self.client, "incidentFilters", "IncidentFilter", desired)
            workshop.reconcile_extended(self.client, "commonprompts", "CommonPrompt", prompts)
        self.assertEqual(len(self.client.extended["incidentFilters"]), len(desired))
        self.assertEqual(len(self.client.extended["commonprompts"]), len(prompts))
        desired[0]["properties"]["isEnabled"] = False
        workshop.reconcile_extended(self.client, "incidentFilters", "IncidentFilter", desired)
        self.assertFalse(self.client.extended["incidentFilters"]["workshop-alerts"]["properties"]["isEnabled"])

    def test_missing_filter_delete_contract_is_not_guessed(self):
        self.client.extended["incidentFilters"]["workshop-retired"] = {"name": "workshop-retired"}
        with self.assertRaisesRegex(workshop.DeploymentError, "isEnabled: false"):
            workshop.reconcile_extended(self.client, "incidentFilters", "IncidentFilter", [])
        self.assertFalse(any(method == "DELETE" for method, _ in self.client.calls))

    def test_owned_prompts_removed_without_affecting_unrelated_prompts(self):
        self.client.extended["commonprompts"] = {"workshop-old": {"name": "workshop-old"}, "unrelated": {"name": "unrelated"}}
        workshop.reconcile_extended(self.client, "commonprompts", "CommonPrompt", [])
        self.assertEqual(set(self.client.extended["commonprompts"]), {"unrelated"})

    def test_knowledge_two_runs_converge_and_preserve_other_files(self):
        _, knowledge = workshop.load_config()
        self.client.files["workshop-retired.md"] = {"filename": "workshop-retired.md"}
        for _ in range(2):
            workshop.sync_documents(self.client, knowledge["documents"])
        self.assertEqual(set(self.client.files), {"unrelated.md"} | {item["name"] for item in knowledge["documents"]})
        self.assertEqual(len(self.client.uploads), 10)
        self.assertIn(knowledge["documents"][0]["content"].encode(), self.client.uploads[0])
        posts = [path for method, path in self.client.calls if method == "POST"]
        self.assertEqual(sum(path.endswith("=true") for path in posts), 2)

    def test_malformed_list_response_fails_closed(self):
        for response in (None, {}, {"value": "not-a-list"}, {"value": [None]}):
            with self.subTest(response=response), self.assertRaises(workshop.DeploymentError):
                workshop.collection(response)

    def test_transient_rbac_error_retried_but_bad_request_is_not(self):
        with patch.object(workshop.time, "sleep"), patch.object(self.client, "request", side_effect=[workshop.ApiError(403), "ok"]) as request:
            self.assertEqual(workshop.retry(lambda: request("GET", "/"), attempts=2), "ok")
            self.assertEqual(request.call_count, 2)
        with patch.object(self.client, "request", side_effect=workshop.ApiError(400)) as request:
            with self.assertRaises(workshop.ApiError):
                workshop.retry(lambda: request("PUT", "/"), attempts=3)
            self.assertEqual(request.call_count, 1)


class BootstrapTests(unittest.TestCase):
    def test_provider_registration_skips_existing_and_waits(self):
        initial = [{"namespace": provider, "state": "Registered"} for provider in workshop.PROVIDERS if provider != "Microsoft.App"]
        final = initial + [{"namespace": "Microsoft.App", "state": "Registered"}]
        with patch.object(workshop, "az", side_effect=[initial, None, final]) as cli, patch.object(workshop.time, "sleep"):
            workshop.register_providers()
        self.assertEqual(cli.call_args_list[1].args, ("provider", "register", "--namespace", "Microsoft.App"))

    def test_success_waits_for_exact_job_execution(self):
        values = {"RESOURCE_GROUP": "rg-workshop", "BOOTSTRAP_JOB_NAME": "bootstrap"}
        outputs = [
            {"properties": {"template": {"containers": [{"image": "registry/orders@sha256:test"}]}}},
            None, {"name": "bootstrap-run"},
            {"properties": {"status": "Running"}}, {"properties": {"status": "Succeeded"}},
        ]
        with patch.object(workshop, "az", side_effect=outputs) as cli, patch.object(workshop.time, "sleep"):
            workshop.bootstrap(values)
        self.assertIn("registry/orders@sha256:test", cli.call_args_list[1].args)
        self.assertIn("bootstrap-run", cli.call_args_list[-1].args)

    def test_failed_execution_aborts_deployment(self):
        outputs = [
            {"properties": {"template": {"containers": [{"image": "registry/orders"}]}}},
            None, {"name": "failed-run"}, {"properties": {"status": "Failed"}},
        ]
        with patch.object(workshop, "az", side_effect=outputs):
            with self.assertRaisesRegex(workshop.DeploymentError, "bootstrap job failed"):
                workshop.bootstrap({"RESOURCE_GROUP": "rg-workshop", "BOOTSTRAP_JOB_NAME": "bootstrap"})


if __name__ == "__main__":
    unittest.main()
