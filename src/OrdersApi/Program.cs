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
        app.UseStaticFiles(new StaticFileOptions
        {
            OnPrepareResponse = context =>
            {
                context.Context.Response.Headers.CacheControl = "no-cache";
                context.Context.Response.Headers["X-Content-Type-Options"] = "nosniff";
            }
        });

        app.MapGet("/health/live", () => Results.Ok(new { status = "live", service = serviceName }));
        app.MapGet("/health/ready", async (OrdersRepository repository, CancellationToken cancellationToken) =>
        {
            await repository.ProbeAsync(cancellationToken);
            return Results.Ok(new { status = "ready", service = serviceName });
        });

        app.MapGet("/", (HttpRequest request, IWebHostEnvironment environment) =>
        {
            if (AcceptsHtml(request))
            {
                request.HttpContext.Response.Headers.CacheControl = "no-cache";
                request.HttpContext.Response.Headers["Content-Security-Policy"] =
                    "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; "
                    + "frame-ancestors 'none'; img-src 'self' data:; object-src 'none'; "
                    + "script-src 'self'; style-src 'self'";
                request.HttpContext.Response.Headers["Referrer-Policy"] = "no-referrer";
                request.HttpContext.Response.Headers["X-Content-Type-Options"] = "nosniff";
                var webRoot = environment.WebRootPath ?? Path.Combine(environment.ContentRootPath, "wwwroot");
                return Results.File(Path.Combine(webRoot, "index.html"), "text/html; charset=utf-8");
            }

            return Results.Ok(new
            {
                service = serviceName,
                description = "Contoso Order Services - orders API",
                endpoints = new[] { "/orders", "/orders/{orderId}", "/orders/{orderId}/quantity", "/storage", "/health/live", "/health/ready" }
            });
        });

        app.MapPost("/orders", async (
            OrderRequest order,
            HttpContext httpContext,
            OrdersRepository repository,
            ILogger<Program> logger,
            CancellationToken cancellationToken) =>
        {
            var errors = ValidateOrder(order);
            if (errors.Count > 0)
            {
                return Results.ValidationProblem(errors);
            }

            if (!SampleProducts.TryGetPrice(order.ProductId, out var unitPrice))
            {
                return Results.ValidationProblem(new Dictionary<string, string[]>
                {
                    ["productId"] = ["Unknown productId. Choose SKU-1001, SKU-1002, SKU-1003, SKU-1004 or SKU-1005."]
                });
            }

            if (!TryGetIdempotencyKey(httpContext.Request, out var requestId, out var keyError))
            {
                return Results.ValidationProblem(new Dictionary<string, string[]>
                {
                    ["idempotencyKey"] = [keyError!]
                });
            }

            if (requestId is null)
            {
                var orderId = await repository.CreateOrderAsync(order, unitPrice, cancellationToken);
                logger.LogInformation("Created order {OrderId} for customer {CustomerId}.", orderId, order.CustomerId);
                return Results.Created($"/orders/{orderId}", new { orderId, unitPrice });
            }

            var creation = await repository.CreateOrderIdempotentlyAsync(
                order, unitPrice, requestId, cancellationToken);
            if (creation is null)
            {
                return Results.Problem(
                    title: "Idempotency key conflict",
                    detail: "The Idempotency-Key was already used for a different order request.",
                    statusCode: StatusCodes.Status409Conflict);
            }

            if (creation.Replayed)
            {
                httpContext.Response.Headers["Idempotency-Replayed"] = "true";
                logger.LogInformation("Replayed order creation {OrderId}.", creation.OrderId);
            }
            else
            {
                logger.LogInformation(
                    "Created order {OrderId} for customer {CustomerId}.", creation.OrderId, order.CustomerId);
            }

            return Results.Created(
                $"/orders/{creation.OrderId}",
                new { orderId = creation.OrderId, unitPrice = creation.UnitPrice });
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

    private static bool AcceptsHtml(HttpRequest request)
    {
        var accepted = request.GetTypedHeaders().Accept;
        return accepted is not null && accepted.Any(value =>
            value.Quality.GetValueOrDefault(1) > 0
            && string.Equals(value.MediaType.Value, "text/html", StringComparison.OrdinalIgnoreCase));
    }

    private static bool TryGetIdempotencyKey(
        HttpRequest request,
        out string? requestId,
        out string? error)
    {
        requestId = null;
        error = null;
        if (!request.Headers.TryGetValue("Idempotency-Key", out var values))
        {
            return true;
        }

        if (values.Count != 1)
        {
            error = "Idempotency-Key must be supplied once.";
            return false;
        }

        var value = values[0]!;
        if (value.Length is < 1 or > 128 || !value.All(IsIdempotencyKeyCharacter))
        {
            error = "Idempotency-Key must contain 1 to 128 letters, digits, periods, underscores, colons or hyphens.";
            return false;
        }

        requestId = value;
        return true;
    }

    private static bool IsIdempotencyKeyCharacter(char value) =>
        value is >= 'a' and <= 'z'
            or >= 'A' and <= 'Z'
            or >= '0' and <= '9'
            or '.'
            or '_'
            or ':'
            or '-';

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
