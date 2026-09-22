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
from unittest.mock import call, patch

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
                    patch.object(workshop, "register_providers"), patch.object(workshop, "register_subscription_features"), \
                    patch.object(workshop, "check_network_migration"), patch.dict(os.environ):
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

    def test_fault_credentials_never_reach_the_attendee_process(self):
        values = {"RESOURCE_GROUP": "rg-workshop", "FAULT_CLIENT_JOB_NAME": "fault-client"}
        output = io.StringIO()
        with patch.object(workshop, "environment", return_value=values), \
                patch.object(workshop, "az") as cli, patch.object(workshop, "http") as http, \
                patch.object(workshop, "run_job", return_value="fault-run") as run, \
                patch.object(workshop, "fault_result", return_value={"status": "healthy"}) as result, \
                patch.object(workshop.sys, "stdout", output):
            workshop.fault("status", [])
        cli.assert_not_called()
        http.assert_not_called()
        overrides = run.call_args.args[3]
        self.assertEqual(json.loads(base64.b64decode(overrides["WORKSHOP_REQUEST"])),
                         {"path": "status", "parameters": {}})
        result.assert_called_once_with(values, overrides["WORKSHOP_REQUEST_ID"], "fault-run")
        self.assertEqual(json.loads(output.getvalue()), {"status": "healthy"})


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


class ProviderRegistrationTests(unittest.TestCase):
    def test_registered_providers_ignore_namespace_case(self):
        for transform in (str.lower, str.upper, str.swapcase):
            registered = [{"namespace": transform(provider), "state": "Registered"} for provider in workshop.PROVIDERS]
            with self.subTest(transform=transform.__name__), \
                    patch.object(workshop, "az", return_value=registered) as cli, \
                    patch.object(workshop.time, "sleep") as sleep, patch("builtins.print") as progress:
                workshop.register_providers()
            cli.assert_called_once_with(
                "provider", "list", "--query", "[].{namespace:namespace,state:registrationState}"
            )
            sleep.assert_not_called()
            progress.assert_called_with("All required Azure resource providers are registered.", flush=True)

    def test_provider_registration_skips_existing_and_waits(self):
        initial = [{"namespace": provider.lower(), "state": "Registered"} for provider in workshop.PROVIDERS if provider != "Microsoft.App"]
        registering = initial + [{"namespace": "microsoft.app", "state": "Registering"}]
        final = initial + [{"namespace": "MICROSOFT.APP", "state": "Registered"}]
        with patch.object(workshop, "az", side_effect=[initial, None, registering, final]) as cli, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print") as progress:
            workshop.register_providers()
        self.assertEqual(cli.call_args_list[1].args, ("provider", "register", "--namespace", "Microsoft.App"))
        self.assertEqual(cli.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(10), call(10)])
        progress.assert_has_calls([
            call("Checking required Azure resource providers...", flush=True),
            call("Requesting registration for Microsoft.App...", flush=True),
            call("Waiting for provider registration (1/90): Microsoft.App (not returned)", flush=True),
            call("Waiting for provider registration (2/90): Microsoft.App (Registering)", flush=True),
            call("All required Azure resource providers are registered.", flush=True),
        ])

    def test_registration_can_complete_on_the_last_poll(self):
        initial = [{"namespace": "Microsoft.Insights", "state": "Registering"}]
        final = [{"namespace": "microsoft.insights", "state": "Registered"}]
        with patch.object(workshop, "PROVIDERS", {"Microsoft.Insights"}), \
                patch.object(workshop, "az", side_effect=[initial, None] + [initial] * 89 + [final]) as cli, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print") as progress:
            workshop.register_providers()
        self.assertEqual(cli.call_count, 92)
        self.assertEqual(sleep.call_args_list, [call(10)] * 90)
        progress.assert_called_with("All required Azure resource providers are registered.", flush=True)

    def test_timeout_reports_only_pending_providers_and_their_states(self):
        current = [
            {"namespace": "microsoft.insights", "state": "Registered"},
            {"namespace": "MICROSOFT.APP", "state": "Registering"},
        ]
        with patch.object(workshop, "PROVIDERS", {"Microsoft.App", "Microsoft.Insights", "Microsoft.Sql"}), \
                patch.object(workshop, "az", side_effect=[current, None, None] + [current] * 90) as cli, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print") as progress:
            with self.assertRaisesRegex(workshop.DeploymentError, "within fifteen minutes") as error:
                workshop.register_providers()
        self.assertIn("Microsoft.App (Registering)", str(error.exception))
        self.assertIn("Microsoft.Sql (not returned)", str(error.exception))
        self.assertNotIn("Microsoft.Insights", str(error.exception))
        self.assertEqual(cli.call_count, 93)
        self.assertEqual(sleep.call_args_list, [call(10)] * 90)
        progress.assert_called_with(
            "Waiting for provider registration (90/90): Microsoft.App (Registering), Microsoft.Sql (not returned)",
            flush=True,
        )

    def test_registration_error_aborts_without_polling(self):
        initial = [{"namespace": "microsoft.app", "state": "NotRegistered"}]
        with patch.object(workshop, "PROVIDERS", {"Microsoft.App"}), \
                patch.object(workshop, "az", side_effect=[initial, workshop.DeploymentError("Registration denied")]), \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print"):
            with self.assertRaisesRegex(workshop.DeploymentError, "Registration denied"):
                workshop.register_providers()
        sleep.assert_not_called()


class SubscriptionFeatureRegistrationTests(unittest.TestCase):
    def test_registered_feature_refreshes_its_provider(self):
        feature = ("Microsoft.Network", "AllowBringYourOwnPublicIpAddress")
        with patch.object(workshop, "SUBSCRIPTION_FEATURES", {feature}), \
                patch.object(workshop, "az", side_effect=["Registered", None]) as cli, \
                patch.object(workshop, "register_providers") as providers, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print") as progress:
            workshop.register_subscription_features()
        self.assertEqual(cli.call_args_list, [
            call("feature", "show", "--namespace", feature[0], "--name", feature[1],
                 "--query", "properties.state"),
            call("provider", "register", "--namespace", feature[0]),
        ])
        providers.assert_called_once_with({"Microsoft.Network"})
        sleep.assert_not_called()
        progress.assert_called_with("All required Azure subscription features are registered.", flush=True)

    def test_feature_registration_waits_and_is_case_insensitive(self):
        feature = ("Microsoft.Network", "AllowBringYourOwnPublicIpAddress")
        with patch.object(workshop, "SUBSCRIPTION_FEATURES", {feature}), \
                patch.object(workshop, "az", side_effect=["NotRegistered", None, "REGISTERING", "registered", None]) as cli, \
                patch.object(workshop, "register_providers") as providers, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print") as progress:
            workshop.register_subscription_features()
        self.assertEqual(
            cli.call_args_list[1],
            call("feature", "register", "--namespace", feature[0], "--name", feature[1]),
        )
        self.assertEqual(sleep.call_args_list, [call(10), call(10)])
        providers.assert_called_once_with({"Microsoft.Network"})
        progress.assert_has_calls([
            call(f"Requesting registration for {feature[0]}/{feature[1]}...", flush=True),
            call(
                f"Waiting for subscription feature registration (1/90): "
                f"{feature[0]}/{feature[1]} (NotRegistered)",
                flush=True,
            ),
            call(
                f"Waiting for subscription feature registration (2/90): "
                f"{feature[0]}/{feature[1]} (REGISTERING)",
                flush=True,
            ),
        ])

    def test_feature_registration_timeout_reports_last_state(self):
        feature = ("Microsoft.Network", "AllowBringYourOwnPublicIpAddress")
        with patch.object(workshop, "SUBSCRIPTION_FEATURES", {feature}), \
                patch.object(workshop, "az", side_effect=["Pending"] + ["Pending"] * 90), \
                patch.object(workshop, "register_providers") as providers, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print"):
            with self.assertRaisesRegex(workshop.DeploymentError, "within fifteen minutes") as error:
                workshop.register_subscription_features()
        self.assertIn(f"{feature[0]}/{feature[1]} (Pending)", str(error.exception))
        self.assertIn("Preview features", str(error.exception))
        self.assertEqual(sleep.call_args_list, [call(10)] * 90)
        providers.assert_not_called()

    def test_feature_registration_rejects_invalid_state_response(self):
        with patch.object(workshop, "az", return_value=None), \
                patch.object(workshop.time, "sleep") as sleep:
            with self.assertRaisesRegex(workshop.DeploymentError, "invalid registration state"):
                workshop.register_subscription_features()
        sleep.assert_not_called()


class ManagedIdentityNamingTests(unittest.TestCase):
    def test_identity_names_describe_their_workload_and_purpose(self):
        main = (workshop.ROOT / "infra/main.bicep").read_text()
        apps = (workshop.ROOT / "infra/apps.bicep").read_text()
        sre_agent = (workshop.ROOT / "infra/sre-agent.bicep").read_text()
        expected = {
            "id-orders-api-${suffix}": (main, apps),
            "id-catalog-api-${suffix}": (main, apps),
            "id-orders-db-bootstrap-${suffix}": (main, apps),
            "id-fault-token-init-${suffix}": (main,),
            "id-fault-client-${suffix}": (main, apps),
            "id-sre-agent-runtime-${suffix}": (sre_agent,),
        }
        for resource_name, files in expected.items():
            with self.subTest(resource_name=resource_name):
                for content in files:
                    self.assertIn(f"name: '{resource_name}'", content)
        for retired_name in (
            "id-${suffix}", "id-catalog-${suffix}", "id-bootstrap-${suffix}",
            "id-token-${suffix}", "id-fault-${suffix}", "id-sre-${suffix}",
        ):
            with self.subTest(retired_name=retired_name):
                self.assertNotIn(f"name: '{retired_name}'", main + apps + sre_agent)


class PrivateAccessTests(unittest.TestCase):
    def test_fresh_and_partial_deployments_do_not_require_app_deletion(self):
        for responses in ([False], [True, [], []], [True, [{
            "name": "orders-api", "environmentId": "/managedEnvironments/cae-private-workshop",
        }], [{"environmentId": "/managedEnvironments/cae-private-workshop"}]]):
            with self.subTest(responses=responses), patch.object(workshop, "az", side_effect=responses) as cli:
                workshop.check_network_migration({"AZURE_ENV_NAME": "workshop"})
                self.assertFalse(any("delete" in item.args for item in cli.call_args_list))

    def test_existing_non_vnet_apps_fail_before_an_unsupported_move(self):
        with patch.object(workshop, "az", side_effect=[True, [{
            "name": "orders-api", "environmentId": "/managedEnvironments/cae-workshop",
        }]]):
            with self.assertRaisesRegex(workshop.DeploymentError, "new azd environment"):
                workshop.check_network_migration({"AZURE_ENV_NAME": "workshop"})

    def test_existing_non_vnet_job_is_detected_even_when_apps_are_absent(self):
        with patch.object(workshop, "az", side_effect=[True, [], [{
            "environmentId": "/managedEnvironments/cae-workshop",
        }]]):
            with self.assertRaisesRegex(workshop.DeploymentError, "apps or jobs"):
                workshop.check_network_migration({"AZURE_ENV_NAME": "workshop"})

    def test_migration_does_not_treat_malformed_inventory_as_an_empty_group(self):
        for response in (None, "false", {}):
            with self.subTest(response=response), patch.object(workshop, "az", return_value=response):
                with self.assertRaisesRegex(workshop.DeploymentError, "existence response"):
                    workshop.check_network_migration({"AZURE_ENV_NAME": "workshop"})

    def test_secret_reference_is_attached_only_after_private_initialization(self):
        values = {
            "RESOURCE_GROUP": "rg-workshop", "TOKEN_INITIALIZER_JOB_NAME": "token-init",
            "KEY_VAULT_URI": "https://kv-workshop.vault.azure.net/",
            "ORDERS_IDENTITY_RESOURCE_ID": "/identities/orders",
        }
        calls = []
        with patch.object(workshop, "run_job", side_effect=lambda *args: calls.append("initialize")), \
                patch.object(workshop, "az", side_effect=lambda *args: calls.append(args)):
            workshop.initialize_private_access(values)
        self.assertEqual(calls[0], "initialize")
        self.assertEqual(calls[1][:3], ("containerapp", "secret", "set"))
        self.assertIn("fault-token=keyvaultref:https://kv-workshop.vault.azure.net/secrets/fault-token,identityref:/identities/orders", calls[1])
        self.assertIn("Fault__Token=secretref:fault-token", calls[2])
        self.assertIn("Fault__Enabled=true", calls[2])

    def test_failed_initialization_cannot_enable_faults(self):
        with patch.object(workshop, "run_job", side_effect=workshop.DeploymentError("Initialization failed")), \
                patch.object(workshop, "az") as cli:
            with self.assertRaisesRegex(workshop.DeploymentError, "Initialization failed"):
                workshop.initialize_private_access({})
        cli.assert_not_called()

    def test_job_start_merges_overrides_into_the_complete_template(self):
        saved = {
            "containers": [
                {
                    "name": "workshop-private-client", "image": "mcr.microsoft.com/azure-cli:2.64.0",
                    "command": ["python3"], "args": ["-c", "job source"],
                    "resources": {"cpu": 0.25, "memory": "0.5Gi"},
                    "env": [
                        {"name": "KEY_VAULT_URI", "value": "https://kv-workshop.vault.azure.net"},
                        {"name": "WORKSHOP_REQUEST_ID", "value": ""},
                        {"name": "existing-reference", "secretRef": "retained-reference"},
                    ],
                },
                {"name": "sidecar", "image": "example/sidecar", "env": []},
            ],
            "initContainers": [{"name": "setup", "image": "example/setup"}],
        }
        original = copy.deepcopy(saved)
        captured = {}

        def azure(*args):
            if args[:3] == ("containerapp", "job", "show"):
                return saved
            if args[:3] == ("containerapp", "job", "start"):
                path = Path(args[args.index("--yaml") + 1])
                captured["path"] = path
                captured["template"] = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertNotIn("--env-vars", args)
                self.assertNotIn("--container-name", args)
                return {"name": "/jobs/client/executions/client-run"}
            if args[:4] == ("containerapp", "job", "execution", "show"):
                return {"properties": {"status": "Succeeded"}}
            self.fail(f"Unexpected CLI operation: {args[:4]}")

        with patch.object(workshop, "az", side_effect=azure):
            execution = workshop.run_job(
                {"RESOURCE_GROUP": "rg-workshop", "FAULT_CLIENT_JOB_NAME": "client"},
                "FAULT_CLIENT_JOB_NAME", "Fault client", {"WORKSHOP_REQUEST_ID": "a" * 32},
            )
        self.assertEqual(execution, "client-run")
        expected = copy.deepcopy(original)
        expected["containers"][0]["env"][1] = {"name": "WORKSHOP_REQUEST_ID", "value": "a" * 32}
        self.assertEqual(captured["template"], expected)
        self.assertEqual(saved, original)
        self.assertFalse(captured["path"].exists())

    def test_execution_template_is_removed_when_start_fails(self):
        captured = []

        def start(*args):
            captured.append(Path(args[args.index("--yaml") + 1]))
            self.assertTrue(captured[0].exists())
            raise workshop.DeploymentError("Start request failed")

        with patch.object(workshop, "private_execution_template", return_value={"containers": []}), \
                patch.object(workshop, "az", side_effect=start):
            with self.assertRaisesRegex(workshop.DeploymentError, "Start request failed"):
                workshop.run_job(
                    {"RESOURCE_GROUP": "rg-workshop", "FAULT_CLIENT_JOB_NAME": "client"},
                    "FAULT_CLIENT_JOB_NAME", "Fault client", {"WORKSHOP_REQUEST_ID": "a" * 32},
                )
        self.assertFalse(captured[0].exists())

    def test_malformed_execution_templates_fail_before_start(self):
        for saved in (
            None, {"containers": []},
            {"containers": [{"name": "workshop-private-client", "env": "invalid"}]},
            {"containers": [{"name": "workshop-private-client", "env": [
                {"name": "duplicate", "value": "1"}, {"name": "duplicate", "value": "2"},
            ]}]},
        ):
            with self.subTest(saved=saved), patch.object(workshop, "az", return_value=saved) as cli:
                with self.assertRaises(workshop.DeploymentError):
                    workshop.private_execution_template("rg-workshop", "client", {"WORKSHOP_REQUEST_ID": "a" * 32})
                cli.assert_called_once()

    def test_fault_request_defaults_are_preserved(self):
        with patch.object(workshop, "environment", return_value={}), \
                patch.object(workshop, "run_job", return_value="run") as run, \
                patch.object(workshop, "fault_result", return_value={}), patch("builtins.print"):
            workshop.fault("cpu", [])
        request = json.loads(base64.b64decode(run.call_args.args[3]["WORKSHOP_REQUEST"]))
        self.assertEqual(request, {"path": "cpu", "parameters": {"seconds": 600, "threads": 4}})

    def test_invalid_fault_arguments_do_not_start_a_job(self):
        with patch.object(workshop, "run_job") as run:
            with self.assertRaisesRegex(workshop.DeploymentError, "Too many"):
                workshop.fault("status", ["1"])
        run.assert_not_called()

    def test_uncertain_start_retains_request_id_without_reinjecting(self):
        with patch.object(workshop, "environment", return_value={}), \
                patch.object(workshop, "run_job", side_effect=workshop.DeploymentError("Start failed")) as run, \
                patch.object(workshop, "fault_result") as result:
            with self.assertRaisesRegex(workshop.DeploymentError, "may have executed"):
                workshop.fault("cpu", [])
        run.assert_called_once()
        result.assert_not_called()

    def test_fault_result_waits_for_correlated_logs_without_reinjection(self):
        request_id = "a" * 32
        line = f'WORKSHOP_RESULT:{request_id}:{{"cpuLoadActive":true}}'
        with patch.object(workshop, "az", side_effect=[[], [{"Log_s": line}]]) as cli, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print"):
            result = workshop.fault_result({"LOG_ANALYTICS_CUSTOMER_ID": "workspace"}, request_id, "run")
        self.assertEqual(result, {"cpuLoadActive": True})
        self.assertEqual(cli.call_count, 2)
        self.assertTrue(all(item.args[:3] == ("monitor", "log-analytics", "query") for item in cli.call_args_list))
        self.assertIn(f"WORKSHOP_RESULT:{request_id}:", cli.call_args_list[0].args[6])
        self.assertIn("ContainerGroupName_s startswith 'run'", cli.call_args_list[0].args[6])
        self.assertIn("distinct Log_s", cli.call_args_list[0].args[6])
        sleep.assert_called_once_with(5)

    def test_fault_result_timeout_explains_read_only_recovery(self):
        request_id = "a" * 32
        with patch.object(workshop, "az", return_value=[]) as cli, \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print"):
            with self.assertRaisesRegex(workshop.DeploymentError, f"fault-result {request_id}"):
                workshop.fault_result({"LOG_ANALYTICS_CUSTOMER_ID": "workspace"}, request_id, "run")
        self.assertEqual(cli.call_count, 60)
        self.assertEqual(sleep.call_args_list, [call(5)] * 60)

    def test_fault_result_deadline_includes_query_time(self):
        with patch.object(workshop, "az", return_value=[]) as cli, \
                patch.object(workshop.time, "monotonic", side_effect=[0, 301]), \
                patch.object(workshop.time, "sleep") as sleep, patch("builtins.print"):
            with self.assertRaisesRegex(workshop.DeploymentError, "Do not reinject"):
                workshop.fault_result({"LOG_ANALYTICS_CUSTOMER_ID": "workspace"}, "a" * 32, "run")
        cli.assert_called_once()
        sleep.assert_not_called()

    def test_query_failure_retains_request_id_and_read_only_recovery(self):
        with patch.object(workshop, "az", side_effect=workshop.DeploymentError("Query denied")):
            with self.assertRaisesRegex(workshop.DeploymentError, "fault-result " + "a" * 32):
                workshop.fault_result({"LOG_ANALYTICS_CUSTOMER_ID": "workspace"}, "a" * 32, "run")

    def test_conflicting_job_results_do_not_report_success(self):
        prefix = "WORKSHOP_RESULT:" + "a" * 32 + ":"
        with patch.object(workshop, "az", return_value=[
            {"Log_s": prefix + '{"cpuLoadActive":true}'},
            {"Log_s": prefix + '{"cpuLoadActive":false}'},
        ]):
            with self.assertRaisesRegex(workshop.DeploymentError, "conflicting result"):
                workshop.fault_result({"LOG_ANALYTICS_CUSTOMER_ID": "workspace"}, "a" * 32, "run")

    def test_read_only_recovery_does_not_assert_unknown_execution_success(self):
        with patch.object(workshop, "az", return_value=[]), \
                patch.object(workshop.time, "monotonic", side_effect=[0, 301]), patch("builtins.print"):
            with self.assertRaises(workshop.DeploymentError) as error:
                workshop.fault_result({"LOG_ANALYTICS_CUSTOMER_ID": "workspace"}, "a" * 32)
        self.assertNotIn("succeeded", str(error.exception))

    def test_fault_result_rejects_uncorrelated_or_malformed_records(self):
        for rows in (
            {}, [{"Log_s": "WORKSHOP_RESULT:other:{}"}],
            [{"Log_s": "WORKSHOP_RESULT:" + "a" * 32 + ":[]"}],
            [{"Log_s": "WORKSHOP_RESULT:" + "a" * 32 + ":not-json"}],
        ):
            with self.subTest(rows=rows), patch.object(workshop, "az", return_value=rows):
                with self.assertRaises(workshop.DeploymentError):
                    workshop.fault_result({"LOG_ANALYTICS_CUSTOMER_ID": "workspace"}, "a" * 32, "run")


class BootstrapTests(unittest.TestCase):
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
