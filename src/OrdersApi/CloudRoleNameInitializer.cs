using Microsoft.ApplicationInsights.Channel;
using Microsoft.ApplicationInsights.Extensibility;

namespace OrdersApi;

/// <summary>Stamps every telemetry item with the service name so AppRoleName is queryable in KQL.</summary>
public sealed class CloudRoleNameInitializer(string roleName) : ITelemetryInitializer
{
    public void Initialize(ITelemetry telemetry)
    {
        telemetry.Context.Cloud.RoleName = roleName;
    }
}
