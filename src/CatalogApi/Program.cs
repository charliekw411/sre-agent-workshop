using CatalogApi;
using Microsoft.ApplicationInsights.Extensibility;

var builder = WebApplication.CreateBuilder(args);

var serviceName = builder.Configuration["SERVICE_NAME"] ?? "catalog-api";

builder.Services.AddApplicationInsightsTelemetry();
builder.Services.AddSingleton<ITelemetryInitializer>(new CloudRoleNameInitializer(serviceName));
builder.Services.AddProblemDetails();

var app = builder.Build();

var catalog = new Dictionary<string, CatalogProduct>(StringComparer.OrdinalIgnoreCase)
{
    ["SKU-1001"] = new("SKU-1001", "Contoso Mechanical Keyboard", 129.99m),
    ["SKU-1002"] = new("SKU-1002", "Contoso 27in Monitor", 349.00m),
    ["SKU-1003"] = new("SKU-1003", "Contoso Docking Station", 219.50m),
    ["SKU-1004"] = new("SKU-1004", "Contoso Wireless Mouse", 45.75m),
    ["SKU-1005"] = new("SKU-1005", "Contoso Noise Cancelling Headset", 189.00m)
};

app.MapGet("/health/live", () => Results.Ok(new { status = "live", service = serviceName }));
app.MapGet("/health/ready", () => Results.Ok(new { status = "ready", service = serviceName }));

app.MapGet("/", () => Results.Ok(new
{
    service = serviceName,
    description = "Contoso Order Services - catalog API",
    productCount = catalog.Count
}));

app.MapGet("/catalog", () => Results.Ok(catalog.Values));

app.MapGet("/catalog/{productId}", (
    string productId,
    int? failRatePercent,
    ILogger<Program> logger) =>
{
    // The caller passes failRatePercent only while a Module 08 fault is active. Nothing
    // outside the workshop ever sets it, and the default behaviour is a normal lookup.
    if (failRatePercent is > 0 && Random.Shared.Next(100) < failRatePercent)
    {
        logger.LogError(
            "Catalog lookup for {ProductId} failed: pricing provider unavailable.", productId);
        return Results.Problem(
            title: "Pricing provider unavailable",
            detail: "The downstream pricing provider did not respond within the allowed time.",
            statusCode: StatusCodes.Status503ServiceUnavailable);
    }

    if (!catalog.TryGetValue(productId, out var product))
    {
        return Results.NotFound(new { productId, message = "Product not found." });
    }

    return Results.Ok(product);
});

app.Run();

internal sealed record CatalogProduct(string ProductId, string Name, decimal Price);
