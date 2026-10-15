"""Offline regression coverage for cohort deployment and cleanup."""

import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import call, patch

import cohort


SUBSCRIPTIONS = [
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "tenantId": "10000000-0000-0000-0000-000000000001",
        "name": "Subscription One",
        "label": "subscription-1",
    },
    {
        "id": "00000000-0000-0000-0000-000000000002",
        "tenantId": "10000000-0000-0000-0000-000000000001",
        "name": "Subscription Two",
        "label": "subscription-2",
    },
    {
        "id": "00000000-0000-0000-0000-000000000003",
        "tenantId": "10000000-0000-0000-0000-000000000001",
        "name": "Subscription Three",
        "label": "subscription-3",
    },
]


def arguments(participants):
    return argparse.Namespace(
        participants=participants,
        user_prefix="sre-test-workshop-participant",
        user_domain="mngenvmcap115085.onmicrosoft.com",
        location="australiaeast",
        vm_size="Standard_D2as_v5",
        subscriptions=[item["id"] for item in SUBSCRIPTIONS],
    )


class ParticipantTests(unittest.TestCase):
    def test_five_participants_create_only_five_matching_scopes(self):
        records = cohort.participant_records(
            5,
            SUBSCRIPTIONS,
            "sre-test-workshop-participant",
            "mngenvmcap115085.onmicrosoft.com",
        )

        self.assertEqual(
            [item["resourceGroup"] for item in records],
            ["rg-01", "rg-02", "rg-03", "rg-04", "rg-05"],
        )
        self.assertEqual(
            records[0]["userPrincipalName"],
            "sre-test-workshop-participant-01@mngenvmcap115085.onmicrosoft.com",
        )
        self.assertEqual(
            [item["subscriptionLabel"] for item in records],
            [
                "subscription-1", "subscription-2", "subscription-3",
                "subscription-1", "subscription-2",
            ],
        )

    def test_twenty_participants_balance_seven_seven_six(self):
        records = cohort.participant_records(
            20, SUBSCRIPTIONS, "participant", "example.onmicrosoft.com",
        )
        counts = {
            subscription["label"]: sum(
                item["subscriptionLabel"] == subscription["label"]
                for item in records
            )
            for subscription in SUBSCRIPTIONS
        }
        self.assertEqual(
            counts,
            {"subscription-1": 7, "subscription-2": 7, "subscription-3": 6},
        )

    def test_participant_count_is_bounded_by_provisioned_users(self):
        for count in (0, 21):
            with self.subTest(count=count), self.assertRaisesRegex(
                    cohort.CohortError, "between 1 and 20"):
                cohort.participant_records(
                    count, SUBSCRIPTIONS, "participant", "example.com",
                )


class StateTests(unittest.TestCase):
    def paths(self, temporary):
        root = Path(temporary)
        return (
            patch.object(cohort, "STATE_DIRECTORY", root),
            patch.object(cohort, "STATE_PATH", root / "state.json"),
            patch.object(cohort, "ROSTER_PATH", root / "roster.json"),
            patch.object(cohort, "LOG_DIRECTORY", root / "logs"),
        )

    def test_saved_cohort_can_expand_but_not_remap_or_shrink(self):
        with tempfile.TemporaryDirectory() as temporary:
            patches = self.paths(temporary)
            with patches[0], patches[1], patches[2], patches[3]:
                state = cohort.load_or_create_state(arguments(5), SUBSCRIPTIONS)
                self.assertEqual(len(state["participants"]), 5)

                state = cohort.load_or_create_state(arguments(20), SUBSCRIPTIONS)
                self.assertEqual(len(state["participants"]), 20)
                self.assertEqual(state["participants"][19]["resourceGroup"], "rg-20")

                with self.assertRaisesRegex(cohort.CohortError, "cannot reduce"):
                    cohort.load_or_create_state(arguments(5), SUBSCRIPTIONS)

    def test_safe_roster_excludes_tenant_subscription_and_user_identifiers(self):
        state = {
            "participants": [{
                **cohort.participant_records(
                    1, SUBSCRIPTIONS, "participant", "secret.example.com",
                )[0],
                "userObjectId": "20000000-0000-0000-0000-000000000001",
            }]
        }
        serialized = json.dumps(cohort.safe_roster(state))

        self.assertIn("participant-01", serialized)
        self.assertIn("subscription-1", serialized)
        self.assertNotIn(SUBSCRIPTIONS[0]["id"], serialized)
        self.assertNotIn(SUBSCRIPTIONS[0]["tenantId"], serialized)
        self.assertNotIn("secret.example.com", serialized)
        self.assertNotIn("20000000-0000-0000-0000-000000000001", serialized)


class SafetyTests(unittest.TestCase):
    def item(self):
        return {
            **cohort.participant_records(
                1, SUBSCRIPTIONS, "participant", "example.com",
            )[0],
            "userObjectId": "20000000-0000-0000-0000-000000000001",
        }

    def test_existing_resource_group_requires_exact_workshop_tags(self):
        item = self.item()
        with patch.object(cohort.workshop, "az", side_effect=[
            True,
            {
                "location": "australiaeast",
                "tags": {"workshop-architecture": cohort.ARCHITECTURE},
            },
        ]) as az:
            with self.assertRaisesRegex(cohort.CohortError, "matching workshop tags"):
                cohort.inspect_resource_group(
                    item, "australiaeast", "Standard_D2as_v5",
                )
        self.assertEqual(az.call_count, 2)
        self.assertNotIn("delete", str(az.call_args_list))

    def test_user_resolution_explicitly_requests_account_enabled(self):
        item = self.item()
        state = {
            "subscriptions": SUBSCRIPTIONS,
            "participants": [item],
        }
        with patch.object(cohort.workshop, "az",
                return_value={
                    "id": item["userObjectId"],
                    "userPrincipalName": item["userPrincipalName"],
                    "accountEnabled": True,
                    "userType": "Member",
                }) as az:
            cohort.resolve_users(state)

        self.assertIn("%40", az.call_args.args[4])
        self.assertIn("$select=id,userPrincipalName,accountEnabled,userType",
                      az.call_args.args[4])

    def test_disabled_user_is_rejected(self):
        item = self.item()
        state = {
            "subscriptions": SUBSCRIPTIONS,
            "participants": [item],
        }
        with patch.object(cohort.workshop, "az",
                return_value={
                    "id": item["userObjectId"],
                    "userPrincipalName": item["userPrincipalName"],
                    "accountEnabled": False,
                }), self.assertRaisesRegex(cohort.CohortError, "is disabled"):
            cohort.resolve_users(state)

    def test_parallel_deployment_is_bounded_per_subscription(self):
        items = cohort.participant_records(
            5, SUBSCRIPTIONS, "participant", "example.com",
        )
        state = {"subscriptions": SUBSCRIPTIONS, "participants": items}
        lock = threading.Lock()
        active = {item["id"]: 0 for item in SUBSCRIPTIONS}
        maximum = {item["id"]: 0 for item in SUBSCRIPTIONS}
        total_active = 0
        maximum_total = 0

        def deploy(_, item):
            nonlocal total_active, maximum_total
            with lock:
                subscription = item["subscriptionId"]
                active[subscription] += 1
                total_active += 1
                maximum[subscription] = max(
                    maximum[subscription], active[subscription],
                )
                maximum_total = max(maximum_total, total_active)
            time.sleep(0.03)
            with lock:
                active[subscription] -= 1
                total_active -= 1

        with patch.object(cohort, "deploy_participant", side_effect=deploy):
            cohort.deploy_parallel(state, items, parallel_per_subscription=2)

        self.assertTrue(all(value <= 2 for value in maximum.values()))
        self.assertGreater(maximum_total, 1)

    def test_deployment_reports_completed_stages(self):
        item = self.item()
        state = {"participants": [item]}

        def update(_, target, **changes):
            target.update(changes)

        with patch.object(cohort, "update_item", side_effect=update), \
                patch.object(cohort, "run_logged"), \
                patch.object(cohort, "ensure_participant_access"), \
                patch.object(cohort, "progress") as progress:
            cohort.deploy_participant(state, item)

        messages = [invocation.args[0] for invocation in progress.call_args_list]
        self.assertIn(
            "[participant-01] Infrastructure succeeded for rg-01; "
            "running validation",
            messages,
        )
        self.assertIn("[participant-01] Validating: smoke", messages)
        self.assertIn("[participant-01] Validating: inspect", messages)
        self.assertIn("[participant-01] Validating: telemetry", messages)
        self.assertIn("[participant-01] Validating: fault status", messages)
        self.assertIn("[participant-01] Validation passed", messages)
        self.assertIn("[participant-01] Assigning participant access", messages)
        self.assertTrue(messages[-1].startswith("[participant-01] Ready:"))

    def test_rbac_retry_skips_successful_deployment_and_validation(self):
        item = self.item()
        item.update({
            "deployment": "Succeeded",
            "validation": "Passed",
            "rbac": "Failed",
            "status": "Failed",
        })
        state = {"participants": [item]}

        def update(_, target, **changes):
            target.update(changes)

        with patch.object(cohort, "update_item", side_effect=update), \
                patch.object(cohort, "run_logged") as run_logged, \
                patch.object(
                    cohort, "ensure_participant_access",
                ) as ensure_participant_access, \
                patch.object(cohort, "progress") as progress:
            cohort.deploy_participant(state, item)

        run_logged.assert_not_called()
        ensure_participant_access.assert_called_once_with(item)
        messages = [invocation.args[0] for invocation in progress.call_args_list]
        self.assertIn(
            "[participant-01] Infrastructure already succeeded for rg-01; "
            "skipping deployment",
            messages,
        )
        self.assertIn(
            "[participant-01] Validation already passed; skipping validation",
            messages,
        )
        self.assertEqual(item["status"], "Ready")
        self.assertEqual(item["rbac"], cohort.PARTICIPANT_ACCESS)

    def test_role_lookup_uses_scope_without_all(self):
        item = self.item()
        with patch.object(cohort.workshop, "az", return_value=[]) as az:
            cohort.role_assignments(
                item, cohort.resource_group_scope(item), cohort.CONTRIBUTOR_ROLE_ID,
            )

        self.assertIn("--scope", az.call_args.args)
        self.assertNotIn("--all", az.call_args.args)

    def test_existing_role_assignment_is_not_duplicated(self):
        item = self.item()
        with patch.object(
                cohort, "role_assignments", return_value=[{"id": "existing"}]), \
                patch.object(cohort.workshop, "az") as az:
            cohort.ensure_role_assignment(
                item,
                cohort.resource_group_scope(item),
                cohort.CONTRIBUTOR_ROLE_ID,
                "Contributor",
            )
        az.assert_not_called()

    def test_contributor_is_created_only_at_matching_resource_group(self):
        item = self.item()
        with patch.object(
                cohort, "role_assignments",
                side_effect=[[], [{"id": "created"}]]), \
                patch.object(cohort.workshop, "az", return_value={}) as az:
            cohort.ensure_role_assignment(
                item,
                cohort.resource_group_scope(item),
                cohort.CONTRIBUTOR_ROLE_ID,
                "Contributor",
            )

        scope = (
            f"/subscriptions/{item['subscriptionId']}"
            f"/resourceGroups/{item['resourceGroup']}"
        )
        self.assertEqual(az.call_count, 1)
        self.assertEqual(
            az.call_args,
            call(
                "role", "assignment", "create",
                "--assignee-object-id", item["userObjectId"],
                "--assignee-principal-type", "User",
                "--role", cohort.CONTRIBUTOR_ROLE_ID,
                "--scope", scope,
                "--subscription", item["subscriptionId"],
            ),
        )

    def test_participant_gets_standard_user_only_on_matching_agent(self):
        item = self.item()
        agent_scope = (
            cohort.resource_group_scope(item)
            + "/providers/Microsoft.App/agents/sre-test"
        )
        with patch.object(
                cohort.workshop, "az",
                return_value=[{"id": agent_scope}]) as az, \
                patch.object(cohort, "ensure_role_assignment") as ensure:
            cohort.ensure_participant_access(item)

        az.assert_called_once_with(
            "resource", "list",
            "--resource-group", item["resourceGroup"],
            "--resource-type", "Microsoft.App/agents",
            "--subscription", item["subscriptionId"],
        )
        self.assertEqual(
            ensure.call_args_list,
            [
                call(
                    item,
                    cohort.resource_group_scope(item),
                    cohort.CONTRIBUTOR_ROLE_ID,
                    "Contributor",
                ),
                call(
                    item,
                    agent_scope,
                    cohort.SRE_AGENT_STANDARD_USER_ROLE_ID,
                    "SRE Agent Standard User",
                ),
            ],
        )

    def test_runtime_principal_comes_from_the_agents_only_uami(self):
        item = self.item()
        identity_id = (
            cohort.resource_group_scope(item)
            + "/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-sre-test"
        )
        principal = "30000000-0000-0000-0000-000000000001"
        with patch.object(cohort.workshop, "az", side_effect=[
            [{"identity": {"userAssignedIdentities": {identity_id: {}}}}],
            {"principalId": principal},
        ]) as az:
            self.assertEqual(cohort.sre_agent_runtime_principal(item), principal)

        self.assertEqual(
            az.call_args_list[1],
            call(
                "identity", "show",
                "--ids", identity_id,
                "--subscription", item["subscriptionId"],
            ),
        )

    def test_cleanup_removes_monitoring_assignment_before_resource_group(self):
        item = self.item()
        state = {"participants": [item]}
        principal = "30000000-0000-0000-0000-000000000001"
        with patch.object(cohort.workshop, "az", side_effect=[
            True,
            {
                "tags": {
                    "workshop-architecture": cohort.ARCHITECTURE,
                    "azd-env-name": item["environment"],
                },
            },
            None,
            False,
        ]), patch.object(
                cohort, "sre_agent_runtime_principal", return_value=principal
        ), patch.object(
                cohort.workshop, "remove_monitoring_contributor"
        ) as remove:
            cohort.cleanup_target(state, item)

        remove.assert_called_once_with({
            "AZURE_SUBSCRIPTION_ID": item["subscriptionId"],
            "SRE_AGENT_IDENTITY_PRINCIPAL_ID": principal,
        })
        self.assertEqual(item["status"], "Cleaned")

    def test_cleanup_refuses_wrong_tags_without_deleting(self):
        item = self.item()
        state = {"participants": [item]}
        with patch.object(cohort.workshop, "az", side_effect=[
            True,
            {
                "tags": {
                    "workshop-architecture": "another-architecture",
                    "azd-env-name": item["environment"],
                },
            },
        ]) as az:
            with self.assertRaisesRegex(cohort.CohortError, "Refusing to delete"):
                cohort.cleanup_target(state, item)
        self.assertEqual(az.call_count, 2)
        self.assertNotIn("group', 'delete", str(az.call_args_list))

    def test_final_table_contains_safe_display_values(self):
        item = self.item()
        item.update({
            "deployment": "Succeeded",
            "validation": "Passed",
            "rbac": cohort.PARTICIPANT_ACCESS,
            "status": "Ready",
            "durationSeconds": 125,
        })
        rendered = cohort.table({"participants": [item]})
        self.assertIn("participant-01", rendered)
        self.assertIn("rg-01", rendered)
        self.assertIn("2m 05s", rendered)
        self.assertNotIn(item["subscriptionId"], rendered)
        self.assertNotIn(item["userPrincipalName"], rendered)


if __name__ == "__main__":
    unittest.main()
