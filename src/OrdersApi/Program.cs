using System.Net;
using System.Security.Cryptography;
using System.Text;
using Microsoft.ApplicationInsights.Extensibility;
using Microsoft.Data.SqlClient;
using OrdersApi;

var builder = WebApplication.CreateBuilder(args);

var serviceName = builder.Configuration["SERVICE_NAME"] ?? "orders-api";

builder.Services.AddApplicationInsightsTelemetry();
builder.Services.AddSingleton<ITelemetryInitializer>(new CloudRoleNameInitializer(serviceName));
builder.Services.AddSingleton<FaultState>();
builder.Services.AddSingleton<OrdersRepository>();
builder.Services.AddHostedService<SchemaInitializer>();
builder.Services.AddProblemDetails();

builder.Services.AddHttpClient("catalog", client =>
{
    client.BaseAddress = new Uri(builder.Configuration["Catalog:BaseUrl"] ?? "http://catalog-api");
    client.Timeout = TimeSpan.FromSeconds(10);
});

var app = builder.Build();

var faultsEnabled = builder.Configuration.GetValue("Fault:Enabled", false);
var faultToken = builder.Configuration["Fault:Token"];

// ---------------------------------------------------------------------------
// Health probes
// ---------------------------------------------------------------------------

app.MapGet("/health/live", () => Results.Ok(new { status = "live", service = serviceName }));

// Deliberately does not check downstream dependencies. Module 08 asks you to explain
// why that decision keeps an unhealthy replica in rotation during a dependency outage.
app.MapGet("/health/ready", () => Results.Ok(new { status = "ready", service = serviceName }));

app.MapGet("/", () => Results.Ok(new
{
    service = serviceName,
    description = "Contoso Order Services - orders API",
    endpoints = new[] { "/orders", "/health/live", "/health/ready", "/fault/status" }
}));

// ---------------------------------------------------------------------------
// Business endpoints
// ---------------------------------------------------------------------------

app.MapPost("/orders", async (
    OrderRequest request,
    OrdersRepository repository,
    FaultState faults,
    IHttpClientFactory httpClientFactory,
    ILogger<Program> logger,
    CancellationToken cancellationToken) =>
{
    if (string.IsNullOrWhiteSpace(request.CustomerId) || string.IsNullOrWhiteSpace(request.ProductId))
    {
        return Results.ValidationProblem(new Dictionary<string, string[]>
        {
            ["request"] = ["customerId and productId are required."]
        });
    }

    if (request.Quantity is < 1 or > 1000)
    {
        return Results.ValidationProblem(new Dictionary<string, string[]>
        {
            ["quantity"] = ["quantity must be between 1 and 1000."]
        });
    }

    decimal unitPrice;
    try
    {
        unitPrice = await LookupPriceAsync(httpClientFactory, faults, request.ProductId, cancellationToken);
    }
    catch (Exception ex)
    {
        // No circuit breaker on purpose. A failing dependency becomes a customer-facing
        // 500, which is the architectural defect Module 09 asks you to identify.
        logger.LogError(ex, "Catalog lookup failed for product {ProductId}.", request.ProductId);
        return Results.Problem(
            title: "Catalog lookup failed",
            detail: "The catalog service did not return product pricing.",
            statusCode: (int)HttpStatusCode.InternalServerError);
    }

    try
    {
        var orderId = await repository.CreateOrderAsync(request, unitPrice, cancellationToken);
        logger.LogInformation("Created order {OrderId} for customer {CustomerId}.", orderId, request.CustomerId);
        return Results.Created($"/orders/{orderId}", new { orderId, unitPrice });
    }
    catch (SqlException ex)
    {
        logger.LogError(ex, "Order persistence failed with SQL error {Number}.", ex.Number);
        return Results.Problem(
            title: "Order could not be persisted",
            detail: $"The orders database rejected the write (SQL error {ex.Number}).",
            statusCode: (int)HttpStatusCode.InternalServerError);
    }
});

app.MapGet("/orders", async (OrdersRepository repository, CancellationToken cancellationToken) =>
{
    var orders = await repository.GetRecentOrdersAsync(25, cancellationToken);
    return Results.Ok(orders);
});

app.MapGet("/storage", async (OrdersRepository repository, CancellationToken cancellationToken) =>
{
    var (used, max) = await repository.GetStorageUsageAsync(cancellationToken);
    var percent = max > 0 ? Math.Round(used * 100.0 / max, 2) : 0;
    return Results.Ok(new { usedBytes = used, maxBytes = max, usedPercent = percent });
});

// ---------------------------------------------------------------------------
// Fault injection. Workshop only. Never enable outside an isolated lab.
// ---------------------------------------------------------------------------

var faults = app.MapGroup("/fault").AddEndpointFilter(async (context, next) =>
{
    if (!faultsEnabled || string.IsNullOrEmpty(faultToken))
    {
        return Results.NotFound();
    }

    var supplied = context.HttpContext.Request.Headers["X-Fault-Token"].ToString();
    if (!IsTokenValid(supplied, faultToken))
    {
        return Results.Unauthorized();
    }

    return await next(context);
});

faults.MapGet("/status", (FaultState state) => Results.Ok(new
{
    cpuLoadActive = state.CpuLoadActive,
    cpuLoadUntilUtc = state.CpuLoadUntilUtc,
    cpuLoadThreads = state.CpuLoadThreads,
    errorInjectionActive = state.ErrorInjectionActive,
    errorRatePercent = state.ErrorRatePercent,
    errorInjectionUntilUtc = state.ErrorInjectionUntilUtc,
    storagePhase = state.StoragePhase.ToString(),
    storageBytesWritten = state.StorageBytesWritten,
    storageTargetPercent = state.StorageTargetPercent,
    storageMessage = state.StorageMessage,
    notes = state.Notes
}));

faults.MapPost("/cpu", (CpuFaultRequest request, FaultState state, ILogger<Program> logger) =>
{
    var seconds = Math.Clamp(request.Seconds ?? 300, 10, 1800);
    var threads = Math.Clamp(request.Threads ?? Environment.ProcessorCount, 1, 16);

    state.StartCpuLoad(seconds, threads);
    logger.LogWarning("FAULT INJECTED: CPU load, {Threads} threads for {Seconds} seconds.", threads, seconds);

    for (var i = 0; i < threads; i++)
    {
        var worker = new Thread(() =>
        {
            var accumulator = 0.0;
            while (state.CpuLoadActive)
            {
                for (var j = 0; j < 100_000; j++)
                {
                    accumulator += Math.Sqrt(j + accumulator % 97);
                }
            }
        })
        {
            IsBackground = true,
            Name = $"fault-cpu-{i}"
        };
        worker.Start();
    }

    return Results.Accepted(value: new { seconds, threads, until = state.CpuLoadUntilUtc });
});

faults.MapPost("/errors", (ErrorFaultRequest request, FaultState state, ILogger<Program> logger) =>
{
    var rate = Math.Clamp(request.RatePercent ?? 100, 1, 100);
    var ttl = Math.Clamp(request.TtlSeconds ?? 600, 30, 3600);

    state.StartErrorInjection(rate, ttl);
    logger.LogWarning("FAULT INJECTED: catalog dependency failures at {Rate}% for {Ttl} seconds.", rate, ttl);

    return Results.Accepted(value: new { ratePercent = rate, ttlSeconds = ttl, until = state.ErrorInjectionUntilUtc });
});

faults.MapPost("/storage", (
    StorageFaultRequest request,
    FaultState state,
    OrdersRepository repository,
    ILogger<Program> logger) =>
{
    if (state.StoragePhase == StorageFaultPhase.Running)
    {
        return Results.Conflict(new { message = "A storage fill is already running. Call /fault/reset first." });
    }

    var targetPercent = Math.Clamp(request.TargetPercent ?? 95, 10, 99);
    var token = state.StartStorageFill(targetPercent);
    logger.LogWarning("FAULT INJECTED: filling orders database to {Target}% of maximum size.", targetPercent);

    _ = Task.Run(async () =>
    {
        try
        {
            await repository.FillStorageAsync(targetPercent, state.RecordStorageProgress, token);
            state.CompleteStorageFill(StorageFaultPhase.Completed, "Target reached.");
        }
        catch (OperationCanceledException)
        {
            state.CompleteStorageFill(StorageFaultPhase.Idle, "Cancelled.");
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "Storage fill failed.");
            state.CompleteStorageFill(StorageFaultPhase.Failed, ex.Message);
        }
    }, token);

    return Results.Accepted(value: new { targetPercent });
});

faults.MapPost("/storage/release", async (
    OrdersRepository repository,
    FaultState state,
    ILogger<Program> logger,
    CancellationToken cancellationToken) =>
{
    state.Reset();
    await repository.ReleaseStorageAsync(cancellationToken);
    logger.LogWarning("Storage ballast removed and database shrunk.");
    return Results.Ok(new { message = "Ballast removed. Storage percentage recovers within a few minutes." });
});

faults.MapPost("/reset", (FaultState state, ILogger<Program> logger) =>
{
    state.Reset();
    logger.LogWarning("All injected faults cleared.");
    return Results.Ok(new { message = "All faults cleared." });
});

app.Run();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static bool IsTokenValid(string supplied, string expected)
{
    var suppliedBytes = Encoding.UTF8.GetBytes(supplied);
    var expectedBytes = Encoding.UTF8.GetBytes(expected);

    // Fixed-time comparison so a wrong token cannot be discovered byte by byte.
    return suppliedBytes.Length == expectedBytes.Length
        && CryptographicOperations.FixedTimeEquals(suppliedBytes, expectedBytes);
}

static async Task<decimal> LookupPriceAsync(
    IHttpClientFactory httpClientFactory,
    FaultState faults,
    string productId,
    CancellationToken cancellationToken)
{
    var client = httpClientFactory.CreateClient("catalog");
    var path = $"/catalog/{Uri.EscapeDataString(productId)}";

    if (faults.ErrorInjectionActive)
    {
        path += $"?failRatePercent={faults.ErrorRatePercent}";
    }

    // No retry policy with jitter and no circuit breaker. Both omissions are intentional
    // and are the contributing factors you are expected to find in Module 11.
    using var response = await client.GetAsync(path, cancellationToken);
    response.EnsureSuccessStatusCode();

    var product = await response.Content.ReadFromJsonAsync<CatalogProduct>(cancellationToken);
    return product?.Price ?? throw new InvalidOperationException("Catalog returned an empty payload.");
}

internal sealed record CpuFaultRequest(int? Seconds, int? Threads);

internal sealed record ErrorFaultRequest(int? RatePercent, int? TtlSeconds);

internal sealed record StorageFaultRequest(int? TargetPercent);

internal sealed record CatalogProduct(string ProductId, string Name, decimal Price);

/// <summary>Creates the orders schema at startup without blocking the health probes.</summary>
internal sealed class SchemaInitializer(OrdersRepository repository, ILogger<SchemaInitializer> logger)
    : IHostedService
{
    public async Task StartAsync(CancellationToken cancellationToken)
    {
        for (var attempt = 1; attempt <= 5; attempt++)
        {
            try
            {
                await repository.EnsureSchemaAsync(cancellationToken);
                return;
            }
            catch (Exception ex) when (attempt < 5)
            {
                logger.LogWarning(ex, "Schema initialization attempt {Attempt} failed. Retrying.", attempt);
                await Task.Delay(TimeSpan.FromSeconds(5 * attempt), cancellationToken);
            }
        }

        // The service still starts. A database that is unreachable is an incident to
        // investigate, not a reason to crash-loop before telemetry is even emitted.
        logger.LogError("Schema initialization failed after 5 attempts. Order writes will fail.");
    }

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}
