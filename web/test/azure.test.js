import test from "node:test";
import assert from "node:assert/strict";

import {
  AmbiguousOperationError,
  AzureRequestError,
  AzureWorkshopClient,
} from "../src/azure.js";
import { postgresqlFaultRuleBody } from "../src/core.js";

const NSG_ID =
  "/subscriptions/33333333-3333-4333-8333-333333333333/resourceGroups/" +
  "rg-sre-agent-workshop-demo/providers/Microsoft.Network/networkSecurityGroups/nsg-orders-demo";
const ENVIRONMENT = {
  networkSecurityGroup: { id: NSG_ID },
  virtualMachine: { id: `${NSG_ID}/unused/vm` },
};

function rule(access, etag = 'W/"current"') {
  return {
    etag,
    ...postgresqlFaultRuleBody(access),
  };
}

function client() {
  return new AzureWorkshopClient(
    { accessToken: async () => "test-token" },
    { tenantId: "11111111-1111-4111-8111-111111111111" },
  );
}

test("PostgreSQL injection reads, conditionally writes, and verifies the rule", async () => {
  const workshop = client();
  const calls = [];
  workshop.request = async (url, options = {}) => {
    calls.push({ url, options });
    if (calls.length === 1) {
      return { payload: rule("Allow") };
    }
    if (calls.length === 2) {
      assert.equal(options.method, "PUT");
      assert.equal(options.headers["If-Match"], 'W/"current"');
      assert.deepEqual(options.body, postgresqlFaultRuleBody("Deny"));
      return { payload: rule("Deny", 'W/"updated"') };
    }
    return { payload: rule("Deny", 'W/"updated"') };
  };

  const result = await workshop.setPostgresqlFault(ENVIRONMENT, true);

  assert.equal(result.postgresql, "active");
  assert.equal(result.postgresqlAccess, "Deny");
  assert.equal(calls.length, 3);
  assert.ok(calls.every(({ url }) => url.includes("PostgreSqlFaultInjection")));
});

test("PostgreSQL reset remains idempotent and still verifies current state", async () => {
  const workshop = client();
  let reads = 0;
  workshop.request = async (_url, options = {}) => {
    assert.notEqual(options.method, "PUT");
    reads += 1;
    return { payload: rule("Allow") };
  };

  const result = await workshop.setPostgresqlFault(ENVIRONMENT, false);

  assert.equal(result.postgresql, "inactive");
  assert.equal(reads, 2);
});

test("PostgreSQL rule drift fails closed before any write", async () => {
  const workshop = client();
  let calls = 0;
  workshop.request = async () => {
    calls += 1;
    return {
      payload: {
        ...rule("Allow"),
        properties: {
          ...rule("Allow").properties,
          destinationPortRange: "*",
        },
      },
    };
  };

  await assert.rejects(
    workshop.setPostgresqlFault(ENVIRONMENT, true),
    /destinationPortRange/,
  );
  assert.equal(calls, 1);
});

test("an interrupted PostgreSQL write is reported as ambiguous", async () => {
  const workshop = client();
  let calls = 0;
  workshop.request = async (_url, options = {}) => {
    calls += 1;
    if (options.method === "PUT") {
      throw new AzureRequestError("connection lost");
    }
    return { payload: rule("Allow") };
  };

  await assert.rejects(
    workshop.setPostgresqlFault(ENVIRONMENT, true),
    AmbiguousOperationError,
  );
  assert.equal(calls, 2);
});

test("PostgreSQL injection drains the API pool only after the NSG read-back", async () => {
  const workshop = client();
  const events = [];
  workshop.runVmFault = async (_environment, action) => {
    events.push(`vm:${action}`);
    return action === "postgresql-denied"
      ? {
          cpu: "inactive",
          apiRecycled: true,
          postgresqlConnectivity: "unavailable",
          readinessStatusCode: 503,
          ordersStatusCode: 503,
        }
      : { cpu: "inactive" };
  };
  workshop.setPostgresqlFault = async (_environment, inject) => {
    events.push(`postgresql:${inject ? "deny" : "allow"}`);
    return { postgresql: "active", postgresqlAccess: "Deny" };
  };

  const result = await workshop.runFault(ENVIRONMENT, "postgresql");

  assert.equal(result.postgresqlConnectivity, "unavailable");
  assert.equal(result.apiRecycled, true);
  assert.deepEqual(events, [
    "vm:status",
    "postgresql:deny",
    "vm:postgresql-denied",
  ]);
});

test("a failed post-Deny recycle is partial and requires a status check", async () => {
  const workshop = client();
  const events = [];
  workshop.runVmFault = async (_environment, action) => {
    events.push(`vm:${action}`);
    if (action === "postgresql-denied") {
      throw new AzureRequestError("Run Command failed", { status: 500 });
    }
    return { cpu: "inactive" };
  };
  workshop.setPostgresqlFault = async () => {
    events.push("postgresql:deny");
    return { postgresql: "active", postgresqlAccess: "Deny" };
  };

  await assert.rejects(
    workshop.runFault(ENVIRONMENT, "postgresql"),
    (error) =>
      error instanceof AmbiguousOperationError &&
      /NSG rule is verified as Deny/.test(error.message),
  );
  assert.deepEqual(events, [
    "vm:status",
    "postgresql:deny",
    "vm:postgresql-denied",
  ]);
});

test("combined status and PostgreSQL reset verify application data-plane state", async () => {
  const workshop = client();
  const events = [];
  workshop.runVmFault = async (_environment, action) => {
    events.push(`vm:${action}`);
    return {
      cpu: "inactive",
      apiRecycled: action === "postgresql-ready",
      postgresqlConnectivity: "ready",
      readinessStatusCode: 200,
      ordersStatusCode: 200,
    };
  };
  workshop.postgresqlFaultStatus = async () => {
    events.push("postgresql:status");
    return { postgresql: "active", postgresqlAccess: "Deny" };
  };
  workshop.setPostgresqlFault = async (_environment, inject) => {
    events.push(`postgresql:${inject ? "deny" : "allow"}`);
    return { postgresql: "inactive", postgresqlAccess: "Allow" };
  };

  assert.equal((await workshop.runFault(ENVIRONMENT, "status")).postgresql, "active");
  assert.equal(
    (await workshop.runFault(ENVIRONMENT, "reset-postgresql")).postgresql,
    "inactive",
  );
  assert.deepEqual(events, [
    "postgresql:status",
    "vm:postgresql-status",
    "postgresql:allow",
    "vm:postgresql-ready",
  ]);
});

test("a failed post-Allow recycle is partial and reset remains retryable", async () => {
  const workshop = client();
  let attempts = 0;
  workshop.setPostgresqlFault = async () => ({
    postgresql: "inactive",
    postgresqlAccess: "Allow",
  });
  workshop.runVmFault = async (_environment, action) => {
    assert.equal(action, "postgresql-ready");
    attempts += 1;
    if (attempts === 1) {
      throw new AzureRequestError("interrupted");
    }
    return {
      cpu: "inactive",
      apiRecycled: true,
      postgresqlConnectivity: "ready",
    };
  };

  await assert.rejects(
    workshop.runFault(ENVIRONMENT, "reset-postgresql"),
    AmbiguousOperationError,
  );
  assert.equal(
    (await workshop.runFault(ENVIRONMENT, "reset-postgresql")).ok,
    true,
  );
  assert.equal(attempts, 2);
});

test("scenario-specific resets do not mutate the other fault", async () => {
  const workshop = client();
  const events = [];
  workshop.runVmFault = async (_environment, action) => {
    events.push(`vm:${action}`);
    return {
      cpu: action === "reset" ? "inactive" : "active",
      postgresqlConnectivity: action === "postgresql-ready" ? "ready" : undefined,
    };
  };
  workshop.postgresqlFaultStatus = async () => {
    events.push("postgresql:status");
    return { postgresql: "active", postgresqlAccess: "Deny" };
  };
  workshop.setPostgresqlFault = async () => {
    events.push("postgresql:allow");
    return { postgresql: "inactive", postgresqlAccess: "Allow" };
  };

  const cpu = await workshop.runFault(ENVIRONMENT, "reset-cpu");
  assert.equal(cpu.postgresql, "active");
  assert.deepEqual(events, ["vm:reset", "postgresql:status"]);

  events.length = 0;
  const postgresql = await workshop.runFault(
    ENVIRONMENT,
    "reset-postgresql",
  );
  assert.equal(postgresql.cpu, "active");
  assert.deepEqual(events, ["postgresql:allow", "vm:postgresql-ready"]);
});
