import {
  BrowserCacheLocation,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser";

import { ARM_SCOPE, LOG_ANALYTICS_SCOPE, WorkshopError } from "./core.js";

export class AuthenticationRequiredError extends WorkshopError {
  constructor(message, options = {}) {
    super(message, options);
    this.name = "AuthenticationRequiredError";
  }
}

export class WorkshopAuth {
  constructor(config) {
    this.config = config;
    this.instance = null;
    this.account = null;
  }

  assertWorkshopAccount(account) {
    const tokenTenant = String(account?.idTokenClaims?.tid || account?.tenantId || "");
    if (!account || tokenTenant.toLowerCase() !== this.config.tenantId) {
      throw new AuthenticationRequiredError(
        "This account was not authenticated by the configured workshop tenant.",
      );
    }
    return account;
  }

  async initialize() {
    this.instance = new PublicClientApplication({
      auth: {
        clientId: this.config.clientId,
        authority: `https://login.microsoftonline.com/${this.config.tenantId}`,
        redirectUri: this.config.redirectUri,
        postLogoutRedirectUri: this.config.redirectUri,
        navigateToLoginRequestUrl: true,
      },
      cache: {
        cacheLocation: BrowserCacheLocation.SessionStorage,
        storeAuthStateInCookie: false,
      },
      system: {
        allowPlatformBroker: false,
      },
    });
    await this.instance.initialize();

    const redirectResponse = await this.instance.handleRedirectPromise();
    const matchingAccounts = this.instance
      .getAllAccounts()
      .filter((account) => {
        const tenant = String(account?.idTokenClaims?.tid || account?.tenantId || "");
        return tenant.toLowerCase() === this.config.tenantId;
      });
    const account =
      redirectResponse?.account ||
      this.instance.getActiveAccount() ||
      (matchingAccounts.length === 1 ? matchingAccounts[0] : null);

    if (!account) {
      return null;
    }
    this.account = this.assertWorkshopAccount(account);
    this.instance.setActiveAccount(this.account);
    return this.account;
  }

  async connect() {
    if (!this.instance) {
      throw new AuthenticationRequiredError("MSAL has not finished initializing.");
    }
    await this.instance.loginRedirect({
      scopes: [ARM_SCOPE],
      extraScopesToConsent: [LOG_ANALYTICS_SCOPE],
      prompt: "select_account",
      redirectStartPage: window.location.href,
    });
  }

  async accessToken(scope) {
    if (!this.instance || !this.account) {
      throw new AuthenticationRequiredError("Connect to Azure before using workshop controls.");
    }
    this.assertWorkshopAccount(this.account);
    try {
      const response = await this.instance.acquireTokenSilent({
        account: this.account,
        scopes: [scope],
      });
      return response.accessToken;
    } catch (error) {
      if (error instanceof InteractionRequiredAuthError) {
        throw new AuthenticationRequiredError(
          "Your Azure session needs interaction. Select Retry connection to sign in again.",
          { cause: error },
        );
      }
      throw error;
    }
  }

  async disconnect() {
    if (!this.instance) {
      return;
    }
    const account = this.account;
    this.account = null;
    await this.instance.logoutRedirect({
      account,
      postLogoutRedirectUri: this.config.redirectUri,
    });
  }
}
