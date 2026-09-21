"""Offline checks for the VNet-only managed-identity job embedded in Bicep."""

import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("private_job", Path(__file__).with_name("private-job.py"))
job = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(job)


class PrivateJobTests(unittest.TestCase):
    def setUp(self):
        self.token = "a" * 64
        self.settings = {
            "KEY_VAULT_URI": "https://kv-workshop.vault.azure.net/",
            "ORDERS_API_URL": "https://orders.example.azurecontainerapps.io",
            "IDENTITY_CLIENT_ID": "00000000-0000-0000-0000-000000000001",
            "IDENTITY_ENDPOINT": "http://localhost:42356/msi/token",
            "IDENTITY_HEADER": "identity-header-placeholder",
            "WORKSHOP_REQUEST_ID": "b" * 32,
        }
        self.environment = patch.dict(os.environ, self.settings, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_managed_identity_uses_local_endpoint_and_explicit_client_id(self):
        with patch.object(job, "request_json", return_value={"access_token": "access-placeholder"}) as request:
            self.assertEqual(job.vault_access_token(), "access-placeholder")
        self.assertIn("client_id=" + self.settings["IDENTITY_CLIENT_ID"], request.call_args.args[1])
        self.assertIn("resource=https%3A%2F%2Fvault.azure.net", request.call_args.args[1])
        self.assertEqual(request.call_args.args[2], {"X-IDENTITY-HEADER": "identity-header-placeholder"})

    def test_identity_header_cannot_be_sent_to_an_external_endpoint(self):
        with patch.dict(os.environ, {"IDENTITY_ENDPOINT": "http://attacker.example/msi/token"}), \
                patch.object(job, "request_json") as request:
            with self.assertRaises(job.JobError):
                job.vault_access_token()
        request.assert_not_called()

    def test_existing_secret_is_preserved(self):
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", return_value={"value": self.token}) as request, \
                patch.object(job.secrets, "token_hex") as generate:
            self.assertEqual(job.fault_secret(initialize=True), self.token)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], "GET")
        generate.assert_not_called()

    def test_only_a_missing_secret_is_generated(self):
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", side_effect=[job.RequestError(404), {"value": self.token}]) as request, \
                patch.object(job.secrets, "token_hex", return_value=self.token) as generate:
            self.assertEqual(job.fault_secret(initialize=True), self.token)
        self.assertEqual([item.args[0] for item in request.call_args_list], ["GET", "PUT"])
        self.assertEqual(request.call_args.args[3], {"value": self.token})
        generate.assert_called_once_with(32)

    def test_lost_creation_response_does_not_rotate_a_created_secret(self):
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", side_effect=[
                    job.RequestError(404), job.NetworkError("Network unavailable"), {"value": self.token},
                ]) as request, patch.object(job.secrets, "token_hex", return_value=self.token) as generate, \
                patch.object(job.time, "sleep"), patch("builtins.print"):
            self.assertEqual(job.fault_secret(initialize=True), self.token)
        self.assertEqual([item.args[0] for item in request.call_args_list], ["GET", "PUT", "GET"])
        generate.assert_called_once()

    def test_fault_client_cannot_initialize_a_missing_secret(self):
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", side_effect=job.RequestError(404)) as request:
            with self.assertRaisesRegex(job.JobError, "not initialized"):
                job.fault_secret()
        request.assert_called_once()

    def test_invalid_existing_secret_is_not_replaced(self):
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", return_value={"value": "unexpected-value"}) as request:
            with self.assertRaisesRegex(job.JobError, "not replaced"):
                job.fault_secret(initialize=True)
        request.assert_called_once()

    def test_permission_propagation_retries_without_logging_credentials(self):
        output = io.StringIO()
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", side_effect=[job.RequestError(403), {"value": self.token}]), \
                patch.object(job.time, "sleep") as sleep, patch.object(job.sys, "stdout", output):
            self.assertEqual(job.fault_secret(), self.token)
        sleep.assert_called_once()
        self.assertIn("Waiting for private Key Vault", output.getvalue())
        self.assertNotIn(self.token, output.getvalue())
        self.assertNotIn("access-placeholder", output.getvalue())

    def test_non_transient_authentication_errors_are_not_retried(self):
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", side_effect=job.RequestError(401)), \
                patch.object(job.time, "sleep") as sleep:
            with self.assertRaises(job.RequestError):
                job.fault_secret()
        sleep.assert_not_called()

    def test_private_access_timeout_is_explicit(self):
        with patch.object(job, "vault_access_token", return_value="access-placeholder"), \
                patch.object(job, "request_json", side_effect=job.RequestError(403)), \
                patch.object(job.time, "monotonic", side_effect=[0, 601]), \
                patch.object(job.time, "sleep") as sleep:
            with self.assertRaisesRegex(job.JobError, "private DNS"):
                job.fault_secret(initialize=True)
        sleep.assert_not_called()

    def test_fault_post_is_not_repeated_after_an_uncertain_response(self):
        with patch.object(job, "fault_secret", return_value=self.token), \
                patch.object(job, "request_json", side_effect=job.RequestError(503)) as request:
            with self.assertRaises(job.RequestError):
                job.invoke_fault({"path": "cpu", "parameters": {"seconds": 600, "threads": 4}})
        request.assert_called_once()
        self.assertEqual(request.call_args.args[0], "POST")

    def test_fault_token_is_only_sent_in_memory_to_the_orders_api(self):
        with patch.object(job, "fault_secret", return_value=self.token), \
                patch.object(job, "request_json", return_value={"cpuLoadActive": False}) as request:
            result = job.invoke_fault({"path": "status", "parameters": {}})
        self.assertEqual(request.call_args.args[0], "GET")
        self.assertEqual(request.call_args.args[1], self.settings["ORDERS_API_URL"] + "/fault/status")
        self.assertEqual(request.call_args.args[2]["X-Fault-Token"], self.token)
        self.assertIsNone(request.call_args.args[3])
        self.assertEqual(result, {"cpuLoadActive": False})

    def test_reflected_credentials_cannot_reach_logs(self):
        with patch.object(job, "fault_secret", return_value=self.token), \
                patch.object(job, "request_json", return_value={"unexpected": self.token}):
            with self.assertRaisesRegex(job.JobError, "output withheld"):
                job.invoke_fault({"path": "status", "parameters": {}})

    def test_invalid_paths_and_payloads_are_rejected_before_reading_secrets(self):
        for request in (
            {}, {"path": "../orders", "parameters": {}}, {"path": ["status"], "parameters": {}},
            {"path": "cpu", "parameters": {"seconds": True}}, {"path": "cpu", "parameters": []},
        ):
            with self.subTest(request=request), patch.object(job, "fault_secret") as secret:
                with self.assertRaises(job.JobError):
                    job.invoke_fault(request)
                secret.assert_not_called()

    def test_unsafe_service_endpoints_are_rejected(self):
        for url in (
            "http://kv-workshop.vault.azure.net", "https://kv-workshop.vault.azure.net.attacker.example",
            "https://user@kv-workshop.vault.azure.net", "https://kv-workshop.vault.azure.net/other",
        ):
            with self.subTest(url=url), self.assertRaises(job.JobError):
                job.endpoint(url, ".vault.azure.net")

    def test_http_errors_do_not_expose_response_bodies(self):
        error = job.urllib.error.HTTPError(
            self.settings["KEY_VAULT_URI"], 403, "sensitive-error",
            None, io.BytesIO(b"sensitive-response"),
        )
        with patch.object(job.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(job.RequestError) as raised:
                job.request_json("GET", self.settings["KEY_VAULT_URI"])
        self.assertEqual(raised.exception.status, 403)
        self.assertNotIn("sensitive", str(raised.exception))

    def test_authenticated_redirects_are_refused(self):
        with self.assertRaises(job.JobError):
            job.NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.example")

    def test_stdout_result_is_correlated_and_never_contains_a_credential(self):
        request = base64.b64encode(json.dumps({"path": "status", "parameters": {}}).encode()).decode()
        output = io.StringIO()
        with patch.dict(os.environ, {"WORKSHOP_OPERATION": "fault", "WORKSHOP_REQUEST": request}), \
                patch.object(job, "fault_secret", return_value=self.token), \
                patch.object(job, "request_json", return_value={"cpuLoadActive": False}), \
                patch.object(job.sys, "stdout", output):
            job.main()
        self.assertEqual(output.getvalue(), 'WORKSHOP_RESULT:' + "b" * 32 + ':{"cpuLoadActive":false}\n')
        self.assertNotIn(self.token, output.getvalue())


if __name__ == "__main__":
    unittest.main()
