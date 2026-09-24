using Microsoft.ApplicationInsights.Extensibility;
using OrdersApi;

if (args.Contains("--bootstrap", StringComparer.Ordinal))
{
    Environment.ExitCode = await DatabaseBootstrap.RunAsync(
        args.Where(arg => arg != "--bootstrap").ToArray());
    return;
}

await using var app = Program.CreateApplication(WebApplication.CreateBuilder(args));
await app.RunAsync();

public partial class Program
{
    public static WebApplication CreateApplication(WebApplicationBuilder builder)
    {
        var serviceName = builder.Configuration["SERVICE_NAME"] ?? "orders-api";
        var database = new OrdersDatabase(builder.Configuration.GetConnectionString("OrdersDb"));

        builder.Services.AddApplicationInsightsTelemetry();
        builder.Services.AddSingleton<ITelemetryInitializer>(new CloudRoleNameInitializer(serviceName));
        builder.Services.AddSingleton(database);
        builder.Services.AddSingleton<OrdersRepository>();
        builder.Services.AddHostedService<DatabaseAvailabilityService>();
        builder.Services.AddProblemDetails();
        builder.Services.AddExceptionHandler<SqliteExceptionHandler>();

        var app = builder.Build();
        app.UseExceptionHandler();

        app.MapGet("/health/live", () => Results.Ok(new { status = "live", service = serviceName }));
        app.MapGet("/health/ready", async (OrdersRepository repository, CancellationToken cancellationToken) =>
        {
            await repository.ProbeAsync(cancellationToken);
            return Results.Ok(new { status = "ready", service = serviceName });
        });

        app.MapGet("/", () => Results.Ok(new
        {
            service = serviceName,
            description = "Contoso Order Services - orders API",
            endpoints = new[] { "/orders", "/orders/{orderId}", "/orders/{orderId}/quantity", "/storage", "/health/live", "/health/ready" }
        }));

        app.MapPost("/orders", async (
            OrderRequest request,
            OrdersRepository repository,
            ILogger<Program> logger,
            CancellationToken cancellationToken) =>
        {
            var errors = ValidateOrder(request);
            if (errors.Count > 0)
            {
                return Results.ValidationProblem(errors);
            }

            if (!SampleProducts.TryGetPrice(request.ProductId, out var unitPrice))
            {
                return Results.ValidationProblem(new Dictionary<string, string[]>
                {
                    ["productId"] = ["Unknown productId. Choose SKU-1001, SKU-1002, SKU-1003, SKU-1004 or SKU-1005."]
                });
            }

            var orderId = await repository.CreateOrderAsync(request, unitPrice, cancellationToken);
            logger.LogInformation("Created order {OrderId} for customer {CustomerId}.", orderId, request.CustomerId);
            return Results.Created($"/orders/{orderId}", new { orderId, unitPrice });
        });

        app.MapGet("/orders", async (OrdersRepository repository, CancellationToken cancellationToken) =>
            Results.Ok(await repository.GetRecentOrdersAsync(25, cancellationToken)));

        app.MapGet("/orders/{orderId:long}", async (
            long orderId, OrdersRepository repository, CancellationToken cancellationToken) =>
        {
            var order = await repository.GetOrderAsync(orderId, cancellationToken);
            return order is null ? Results.NotFound() : Results.Ok(order);
        });

        app.MapPut("/orders/{orderId:long}/quantity", async (
            long orderId,
            OrderQuantityRequest request,
            OrdersRepository repository,
            CancellationToken cancellationToken) =>
        {
            if (request.Quantity is < 1 or > 1000)
            {
                return Results.ValidationProblem(new Dictionary<string, string[]>
                {
                    ["quantity"] = ["quantity must be between 1 and 1000."]
                });
            }

            return await repository.UpdateQuantityAsync(orderId, request.Quantity, cancellationToken)
                ? Results.NoContent()
                : Results.NotFound();
        });

        app.MapGet("/storage", async (OrdersRepository repository, CancellationToken cancellationToken) =>
            Results.Ok(await repository.GetStorageUsageAsync(cancellationToken)));

        return app;
    }

    private static Dictionary<string, string[]> ValidateOrder(OrderRequest request)
    {
        var errors = new Dictionary<string, string[]>();
        if (string.IsNullOrWhiteSpace(request.CustomerId) || request.CustomerId.Length > 64)
        {
            errors["customerId"] = ["customerId is required and must not exceed 64 characters."];
        }
        if (string.IsNullOrWhiteSpace(request.ProductId) || request.ProductId.Length > 64)
        {
            errors["productId"] = ["productId is required and must not exceed 64 characters."];
        }
        if (request.Quantity is < 1 or > 1000)
        {
            errors["quantity"] = ["quantity must be between 1 and 1000."];
        }
        return errors;
    }
}

internal sealed record OrderQuantityRequest(int Quantity);
