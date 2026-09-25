import { AuthenticationRequiredError, WorkshopAuth } from "./auth.js";
import {
  AmbiguousOperationError,
  AzureRequestError,
  AzureWorkshopClient,
} from "./azure.js";
import { WorkshopError, validateConfig } from "./core.js";
import { EnvironmentPicker, HeaderConnection, IncidentLauncher } from "./ui.js";

const rawConfig = __WORKSHOP_CONFIG__;

class WorkshopApplication {
  constructor(config) {
    this.config = config;
    this.auth = null;
    this.azure = null;
    this.launchers = new Map();
    this.picker = null;
    this.header = new HeaderConnection({
      connect: () => void this.connect(),
      retry: () => void this.retry(),
      changeEnvironment: () => void this.changeEnvironment(),
      disconnect: () => void this.disconnect(),
    });
    this.state = {
      phase: "not-connected",
      configuredTenant: config.configured ? config.tenantId : "",
      canRetry: Boolean(config.configured),
      account: null,
      environment: null,
      message: "",
      operation: null,
      requiresStatusCheck: false,
      requiresReauthentication: false,
    };
  }

  get connected() {
    return this.state.phase === "connected" && Boolean(this.state.environment);
  }

  setState(update) {
    this.state = { ...this.state, ...update };
    this.header.render(this.state);
    for (const launcher of this.launchers.values()) {
      launcher.update(this.state);
    }
  }

  mount() {
    this.header.mount();
    for (const root of document.querySelectorAll("[data-sre-incident]")) {
      if (this.launchers.has(root)) {
        continue;
      }
      const scenario = root.dataset.sreIncident;
      if (scenario !== "cpu" && scenario !== "disk") {
        continue;
      }
      const launcher = new IncidentLauncher(root, this, scenario);
      this.launchers.set(root, launcher);
      launcher.update(this.state);
    }
    for (const [root, launcher] of this.launchers) {
      if (!root.isConnected) {
        launcher.stopTimer();
        this.launchers.delete(root);
      }
    }
  }

  async start() {
    this.mount();
    if (!this.config.configured) {
      this.setState({
        phase: "error",
        canRetry: false,
        message: this.config.error,
      });
      return;
    }

    try {
      this.auth = new WorkshopAuth(this.config);
      const account = await this.auth.initialize();
      if (!account) {
        this.setState({
          phase: "not-connected",
          account: null,
          environment: null,
          message: "",
          requiresReauthentication: false,
        });
        return;
      }
      await this.completeConnection(account);
    } catch (error) {
      this.setState({
        phase: "error",
        account: this.auth?.account || null,
        environment: null,
        message: this.errorMessage(error),
        requiresReauthentication: this.needsReauthentication(error),
      });
    }
  }

  async connect() {
    if (!this.auth) {
      return;
    }
    this.setState({
      phase: "connecting",
      message: "Redirecting to the configured workshop tenant...",
      requiresReauthentication: false,
    });
    try {
      await this.auth.connect();
    } catch (error) {
      this.setState({
        phase: "error",
        message: this.errorMessage(error),
        requiresReauthentication: this.needsReauthentication(error),
      });
    }
  }

  storageKey(account) {
    return `sre-workshop-environment:${account.homeAccountId}`;
  }

  storedEnvironment(account) {
    try {
      const value = sessionStorage.getItem(this.storageKey(account));
      if (!value) {
        return null;
      }
      const parsed = JSON.parse(value);
      if (
        typeof parsed?.subscriptionId !== "string" ||
        typeof parsed?.resourceGroupId !== "string"
      ) {
        sessionStorage.removeItem(this.storageKey(account));
        return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }

  rememberEnvironment(account, candidate) {
    try {
      sessionStorage.setItem(
        this.storageKey(account),
        JSON.stringify({
          subscriptionId: candidate.subscription.id,
          resourceGroupId: candidate.resourceGroup.id,
        }),
      );
    } catch {
      // Connection remains valid when browser storage is unavailable.
    }
  }

  async chooseEnvironment(candidates, account, forcePrompt = false) {
    const stored = this.storedEnvironment(account);
    const remembered = candidates.find(
      (candidate) =>
        candidate.subscription.id === stored?.subscriptionId &&
        candidate.resourceGroup.id === stored?.resourceGroupId,
    );
    if (!forcePrompt && remembered) {
      return remembered;
    }
    if (!forcePrompt && candidates.length === 1) {
      return candidates[0];
    }
    this.picker ||= new EnvironmentPicker();
    return this.picker.choose(candidates);
  }

  async completeConnection(account, forcePrompt = false) {
    this.setState({
      phase: "connecting",
      account,
      environment: this.state.environment,
      message: "Discovering accessible workshop environments...",
    });
    this.azure = new AzureWorkshopClient(this.auth, this.config);
    const candidates = await this.azure.discoverEnvironments();
    if (!candidates.length) {
      throw new WorkshopError(
        "No accessible resource group has the single-vm workshop tag. Check the " +
          "subscription and participant RBAC assignments.",
      );
    }
    const candidate = await this.chooseEnvironment(candidates, account, forcePrompt);
    if (!candidate) {
      if (forcePrompt && this.state.environment) {
        this.setState({
          phase: "connected",
          message: "",
        });
        return false;
      }
      throw new WorkshopError("Select a workshop environment before using Azure controls.");
    }
    const environment = await this.azure.loadEnvironment(candidate);
    this.rememberEnvironment(account, candidate);
    this.setState({
      phase: "connected",
      account,
      environment,
      message: "",
      requiresReauthentication: false,
      requiresStatusCheck: false,
    });
    return true;
  }

  async retry() {
    if (!this.config.configured) {
      return;
    }
    if (!this.auth?.account || this.state.requiresReauthentication) {
      await this.connect();
      return;
    }
    try {
      await this.completeConnection(this.auth.account);
    } catch (error) {
      this.setState({
        phase: "error",
        message: this.errorMessage(error),
        requiresReauthentication: this.needsReauthentication(error),
      });
    }
  }

  async changeEnvironment() {
    if (!this.auth?.account) {
      return;
    }
    try {
      await this.completeConnection(this.auth.account, true);
    } catch (error) {
      this.setState({
        phase: "error",
        message: this.errorMessage(error),
        requiresReauthentication: this.needsReauthentication(error),
      });
    }
  }

  async disconnect() {
    if (!this.auth) {
      return;
    }
    const account = this.auth.account;
    if (account) {
      try {
        sessionStorage.removeItem(this.storageKey(account));
      } catch {
        // Logout still clears MSAL state when browser storage is unavailable.
      }
    }
    this.setState({
      phase: "connecting",
      message: "Disconnecting from Azure...",
      environment: null,
    });
    try {
      await this.auth.disconnect();
    } catch (error) {
      this.setState({
        phase: "error",
        message: this.errorMessage(error),
        requiresReauthentication: this.needsReauthentication(error),
      });
    }
  }

  async executeFault(action) {
    if (!this.connected || !this.azure) {
      throw new WorkshopError("Connect to an Azure workshop environment first.");
    }
    if (this.state.operation) {
      throw new WorkshopError("Wait for the current VM Run Command operation to finish.");
    }
    this.setState({
      operation: { action },
    });
    try {
      const result = await this.azure.runFault(this.state.environment, action);
      if (action === "reset") {
        this.setState({ requiresStatusCheck: false });
      } else if (action === "status") {
        const inactive = new Set(["inactive", "failed"]);
        this.setState({
          requiresStatusCheck:
            !inactive.has(String(result.cpu || "").toLowerCase()) ||
            !inactive.has(String(result.disk || "").toLowerCase()),
        });
      }
      return result;
    } catch (error) {
      if (error instanceof AmbiguousOperationError) {
        this.setState({ requiresStatusCheck: true });
      }
      throw error;
    } finally {
      this.setState({ operation: null });
    }
  }

  async chartSeries(scenario) {
    if (!this.connected || !this.azure) {
      throw new WorkshopError("Connect to an Azure workshop environment first.");
    }
    return scenario === "cpu"
      ? this.azure.cpuSeries(this.state.environment)
      : this.azure.diskSeries(this.state.environment);
  }

  incidentLinks(scenario) {
    if (!this.connected || !this.azure) {
      return {};
    }
    return this.azure.links(this.state.environment, scenario);
  }

  errorMessage(error) {
    if (error instanceof AmbiguousOperationError) {
      return error.message;
    }
    if (
      error instanceof AzureRequestError ||
      error instanceof AuthenticationRequiredError ||
      error instanceof WorkshopError
    ) {
      return error.message;
    }
    return "The Azure operation failed unexpectedly. Reconnect and try again.";
  }

  needsReauthentication(error) {
    return (
      error instanceof AuthenticationRequiredError ||
      (error instanceof AzureRequestError && error.status === 401)
    );
  }

  reportError(error) {
    if (
      error instanceof AuthenticationRequiredError ||
      (error instanceof AzureRequestError && error.permissionDenied)
    ) {
      this.setState({
        phase: "error",
        message: this.errorMessage(error),
        requiresReauthentication: this.needsReauthentication(error),
      });
    }
  }
}

function boot() {
  const application = new WorkshopApplication(validateConfig(rawConfig));
  application.start();

  if (window.document$?.subscribe) {
    window.document$.subscribe(() => application.mount());
  } else {
    new MutationObserver(() => application.mount()).observe(document.body, {
      childList: true,
      subtree: true,
    });
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot, { once: true });
} else {
  boot();
}
