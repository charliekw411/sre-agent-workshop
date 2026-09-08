---
title: Workshop Variables
description: Complete reference for every shell variable used by the Azure SRE Agent workshop, including where each one is created and how to rebuild the file.
ms.date: 2026-09-08
ms.topic: reference
keywords:
  - environment variables
  - reference
estimated_reading_time: 6
---

## Overview

Every module reads from `.workshop/workshop.env`. This page documents each variable, the module that creates it, and how to rebuild the file if you lose it.

## Variable reference

| Variable                   | Created in | Example                                        | Purpose                                            |
|----------------------------|------------|------------------------------------------------|----------------------------------------------------|
| `WORKSHOP_SUFFIX`          | Module 00  | `sre4821`                                      | Uniqueness suffix for every resource name          |
| `LOCATION`                 | Module 00  | `eastus`                                       | Azure region for the deployment                    |
| `RESOURCE_GROUP`           | Module 00  | `rg-sre-agent-workshop-sre4821`                | Workshop resource group                            |
| `SUBSCRIPTION_ID`          | Module 01  | `00000000-0000-0000-0000-000000000000`         | Target subscription                                |
| `TENANT_ID`                | Module 01  | `00000000-0000-0000-0000-000000000000`         | Microsoft Entra tenant                             |
| `SQL_ADMIN_LOGIN`          | Module 03  | `sreworkshopadmin`                             | SQL logical server administrator login             |
| `SQL_ADMIN_PASSWORD`       | Module 03  | Generated                                      | SQL administrator password (credential)            |
| `FAULT_TOKEN`              | Module 03  | Generated                                      | Shared secret for `/fault` endpoints (credential)  |
| `ALERT_EMAIL`              | Module 03  | `you@example.com`                              | Action group notification target                   |
| `ACR_NAME`                 | Module 03  | `acrsre4821`                                   | Container registry name                            |
| `ACR_LOGIN_SERVER`         | Module 03  | `acrsre4821.azurecr.io`                        | Registry login server for image references         |
| `LOG_ANALYTICS_NAME`       | Module 03  | `law-sre4821`                                  | Log Analytics workspace name                       |
| `LOG_ANALYTICS_ID`         | Module 03  | `/subscriptions/.../workspaces/law-sre4821`    | Workspace resource ID for role assignment          |
| `LOG_ANALYTICS_CUSTOMER_ID`| Module 03  | `00000000-0000-0000-0000-000000000000`         | Workspace GUID used by `az monitor log-analytics query` |
| `APP_INSIGHTS_NAME`        | Module 03  | `appi-sre4821`                                 | Application Insights resource name                 |
| `SQL_SERVER_NAME`          | Module 03  | `sql-sre4821`                                  | SQL logical server name                            |
| `SQL_DATABASE_NAME`        | Module 03  | `sqldb-orders`                                 | Orders database name                               |
| `CONTAINER_ENV_NAME`       | Module 03  | `cae-sre4821`                                  | Container Apps environment name                    |
| `ORDERS_API_FQDN`          | Module 03  | `orders-api.<env>.eastus.azurecontainerapps.io`| Public ingress hostname                            |
| `SRE_AGENT_NAME`           | Module 05  | `sre-agent-workshop`                           | Azure SRE Agent resource name                      |
| `SRE_AGENT_PRINCIPAL_ID`   | Module 05  | `00000000-0000-0000-0000-000000000000`         | Agent managed identity object ID                   |
| `INCIDENT_1_START`         | Module 06  | `2026-09-08T14:02:11Z`                         | UTC timestamp for the CPU incident                 |
| `INCIDENT_2_START`         | Module 08  | `2026-09-08T15:11:42Z`                         | UTC timestamp for the dependency incident          |
| `INCIDENT_3_START`         | Module 10  | `2026-09-08T16:04:07Z`                         | UTC timestamp for the storage incident             |

## Credentials in this file

`SQL_ADMIN_PASSWORD` and `FAULT_TOKEN` are credentials. Treat the file accordingly.

```bash
chmod 600 .workshop/workshop.env
git check-ignore -v .workshop/workshop.env
```

The second command must print a `.gitignore` rule. If it prints nothing, the file is not ignored and you must fix that before committing.

## Rebuilding the file

If you lose the file after deployment, most values can be recovered from Azure. The two generated secrets cannot.

```bash
export WORKSHOP_SUFFIX="<your-suffix>"
export LOCATION="<your-region>"
export RESOURCE_GROUP="rg-sre-agent-workshop-${WORKSHOP_SUFFIX}"
export SUBSCRIPTION_ID="$(az account show --query id --output tsv)"
export TENANT_ID="$(az account show --query tenantId --output tsv)"

export ACR_NAME="$(az acr list --resource-group "${RESOURCE_GROUP}" --query "[0].name" --output tsv)"
export ACR_LOGIN_SERVER="$(az acr list --resource-group "${RESOURCE_GROUP}" --query "[0].loginServer" --output tsv)"
export LOG_ANALYTICS_NAME="$(az monitor log-analytics workspace list --resource-group "${RESOURCE_GROUP}" --query "[0].name" --output tsv)"
export LOG_ANALYTICS_ID="$(az monitor log-analytics workspace list --resource-group "${RESOURCE_GROUP}" --query "[0].id" --output tsv)"
export LOG_ANALYTICS_CUSTOMER_ID="$(az monitor log-analytics workspace list --resource-group "${RESOURCE_GROUP}" --query "[0].customerId" --output tsv)"
export APP_INSIGHTS_NAME="$(az monitor app-insights component show --resource-group "${RESOURCE_GROUP}" --query "[0].name" --output tsv)"
export SQL_SERVER_NAME="$(az sql server list --resource-group "${RESOURCE_GROUP}" --query "[0].name" --output tsv)"
export SQL_DATABASE_NAME="sqldb-orders"
export CONTAINER_ENV_NAME="$(az containerapp env list --resource-group "${RESOURCE_GROUP}" --query "[0].name" --output tsv)"
export ORDERS_API_FQDN="$(az containerapp show --name orders-api --resource-group "${RESOURCE_GROUP}" --query properties.configuration.ingress.fqdn --output tsv)"

mkdir -p .workshop
{
  for v in WORKSHOP_SUFFIX LOCATION RESOURCE_GROUP SUBSCRIPTION_ID TENANT_ID \
           ACR_NAME ACR_LOGIN_SERVER LOG_ANALYTICS_NAME LOG_ANALYTICS_ID \
           LOG_ANALYTICS_CUSTOMER_ID APP_INSIGHTS_NAME SQL_SERVER_NAME \
           SQL_DATABASE_NAME CONTAINER_ENV_NAME ORDERS_API_FQDN; do
    echo "export ${v}=\"${!v}\""
  done
} > .workshop/workshop.env

chmod 600 .workshop/workshop.env
```

## Recovering the generated secrets

The fault token is stored as a container app secret and can be rotated rather than recovered.

```bash
export FAULT_TOKEN="$(openssl rand -hex 24)"

az containerapp secret set \
  --name orders-api \
  --resource-group "${RESOURCE_GROUP}" \
  --secrets "fault-token=${FAULT_TOKEN}" \
  --output none

echo "export FAULT_TOKEN=\"${FAULT_TOKEN}\"" >> .workshop/workshop.env
```

The SQL administrator password cannot be read back. Reset it if you need it.

```bash
export SQL_ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)Aa1!"

az sql server update \
  --name "${SQL_SERVER_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --admin-password "${SQL_ADMIN_PASSWORD}" \
  --output none

echo "export SQL_ADMIN_PASSWORD=\"${SQL_ADMIN_PASSWORD}\"" >> .workshop/workshop.env
```

Redeploy `infra/apps.bicep` afterwards so the container app secret matches the new password.

<div class="sre-nav" markdown>
[:material-arrow-left: About The Authors](../29-about-the-authors/index.md)
[Troubleshooting :material-arrow-right:](02-troubleshooting.md)
</div>
