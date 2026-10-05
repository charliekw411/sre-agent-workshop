using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.ApplicationInsights.AspNetCore.Extensions;
using Microsoft.ApplicationInsights.Channel;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace OrdersApi.Tests;

[Collection(PostgreSqlCollection.Name)]
public sealed class OrdersApiTests(PostgreSqlServerFixture server)
{
    [Fact]
    public async Task CreateReadUpdateAndListOrders()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        var customer = new string('C', 64);

        using var created = await app.Client.PostAsJsonAsync(
            "/orders", new OrderRequest(customer, "SKU-1001", 1));
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var body = await created.Content.ReadFromJsonAsync<JsonElement>();
        var orderId = body.GetProperty("orderId").GetInt64();
        Assert.True(orderId > 0);
        Assert.Equal(129.99m, body.GetProperty("unitPrice").GetDecimal());
        Assert.Equal($"/orders/{orderId}", created.Headers.Location!.ToString());
        var order = await app.Client.GetFromJsonAsync<OrderRecord>(
            created.Headers.Location);
        Assert.Equal(customer, order!.CustomerId);
        Assert.Equal("SKU-1001", order.ProductId);
        Assert.Equal(1, order.Quantity);
        Assert.Equal(DateTimeKind.Utc, order.CreatedUtc.Kind);

        using var updated = await app.Client.PutAsJsonAsync(
            $"/orders/{orderId}/quantity", new { quantity = 1000 });
        Assert.Equal(HttpStatusCode.NoContent, updated.StatusCode);
        order = await app.Client.GetFromJsonAsync<OrderRecord>($"/orders/{orderId}");
        Assert.Equal(1000, order!.Quantity);
        Assert.Equal(129.99m, order.UnitPrice);
        var orders = await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders");
        Assert.Equal(order, orders![0]);
        Assert.Equal(6, orders.Length);

        using var missing = await app.Client.GetAsync("/orders/999999");
        using var missingUpdate = await app.Client.PutAsJsonAsync(
            "/orders/999999/quantity", new { quantity = 2 });
        Assert.Equal(HttpStatusCode.NotFound, missing.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, missingUpdate.StatusCode);
    }

    [Fact]
    public async Task RootNegotiatesBrowserGuiWithoutChangingJsonDescriptor()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);

        using var defaultResponse = await app.Client.GetAsync("/");
        Assert.Equal(HttpStatusCode.OK, defaultResponse.StatusCode);
        Assert.Equal(
            "application/json", defaultResponse.Content.Headers.ContentType!.MediaType);
        var descriptor =
            await defaultResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("orders-api", descriptor.GetProperty("service").GetString());
        Assert.Equal(
            new[]
            {
                "/orders",
                "/orders/{orderId}",
                "/orders/{orderId}/quantity",
                "/database",
                "/health/live",
                "/health/ready"
            },
            descriptor.GetProperty("endpoints").EnumerateArray()
                .Select(item => item.GetString()));

        using var htmlRequest = new HttpRequestMessage(HttpMethod.Get, "/");
        htmlRequest.Headers.Accept.Add(
            new MediaTypeWithQualityHeaderValue("text/html"));
        using var htmlResponse = await app.Client.SendAsync(htmlRequest);
        Assert.Equal(HttpStatusCode.OK, htmlResponse.StatusCode);
        Assert.Equal(
            "text/html", htmlResponse.Content.Headers.ContentType!.MediaType);
        Assert.Equal("utf-8", htmlResponse.Content.Headers.ContentType.CharSet);
        Assert.Contains("default-src 'self'", Assert.Single(
            htmlResponse.Headers.GetValues("Content-Security-Policy")));
        var html = await htmlResponse.Content.ReadAsStringAsync();
        Assert.Contains("<main id=\"main-content\"", html);
        Assert.Contains("<dialog id=\"order-dialog\"", html);
        Assert.Contains("aria-live=\"polite\"", html);
        Assert.Contains("PostgreSQL", html);
        Assert.Contains("href=\"/app.css\"", html);
        Assert.Contains("src=\"/app.js\"", html);
        Assert.DoesNotContain("https://", html);
        Assert.DoesNotContain("/fault", html);

        using var css = await app.Client.GetAsync("/app.css");
        using var javascript = await app.Client.GetAsync("/app.js");
        Assert.Equal(HttpStatusCode.OK, css.StatusCode);
        Assert.Equal(HttpStatusCode.OK, javascript.StatusCode);
        Assert.Equal("text/css", css.Content.Headers.ContentType!.MediaType);
        Assert.Contains(
            "javascript", javascript.Content.Headers.ContentType!.MediaType);
        Assert.Equal(
            "nosniff", Assert.Single(css.Headers.GetValues("X-Content-Type-Options")));
        Assert.Contains(
            "prefers-reduced-motion", await css.Content.ReadAsStringAsync());
        var script = await javascript.Content.ReadAsStringAsync();
        Assert.Contains("Idempotency-Key", script);
        Assert.Contains("/database", script);
    }

    [Fact]
    public async Task IdempotencyKeyReplaysCreateAndRejectsConflictingPayload()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        const string requestId = "browser-request-123";

        static HttpRequestMessage CreateRequest(OrderRequest order)
        {
            var request = new HttpRequestMessage(HttpMethod.Post, "/orders")
            {
                Content = JsonContent.Create(order)
            };
            request.Headers.Add("Idempotency-Key", requestId);
            return request;
        }

        using var firstRequest =
            CreateRequest(new("retry-customer", "SKU-1002", 2));
        using var first = await app.Client.SendAsync(firstRequest);
        using var replayRequest =
            CreateRequest(new("retry-customer", "SKU-1002", 2));
        using var replay = await app.Client.SendAsync(replayRequest);
        Assert.Equal(HttpStatusCode.Created, first.StatusCode);
        Assert.Equal(HttpStatusCode.Created, replay.StatusCode);
        var firstBody = await first.Content.ReadFromJsonAsync<JsonElement>();
        var replayBody = await replay.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(
            firstBody.GetProperty("orderId").GetInt64(),
            replayBody.GetProperty("orderId").GetInt64());
        Assert.Equal(first.Headers.Location, replay.Headers.Location);
        Assert.Equal(
            "true", Assert.Single(replay.Headers.GetValues("Idempotency-Replayed")));

        using var conflictRequest =
            CreateRequest(new("retry-customer", "SKU-1002", 3));
        using var conflict = await app.Client.SendAsync(conflictRequest);
        Assert.Equal(HttpStatusCode.Conflict, conflict.StatusCode);
        Assert.Equal(
            "application/problem+json",
            conflict.Content.Headers.ContentType!.MediaType);
        var problem = await conflict.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(
            "Idempotency key conflict", problem.GetProperty("title").GetString());
        Assert.Equal(
            6, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Fact]
    public async Task InvalidIdempotencyKeyIsRejectedBeforeWriting()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var request = new HttpRequestMessage(HttpMethod.Post, "/orders")
        {
            Content = JsonContent.Create(
                new OrderRequest("customer", "SKU-1001", 1))
        };
        Assert.True(request.Headers.TryAddWithoutValidation(
            "Idempotency-Key", "not valid"));

        using var response = await app.Client.SendAsync(request);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.True(body.GetProperty("errors").TryGetProperty(
            "idempotencyKey", out _));
        Assert.Equal(
            5, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Fact]
    public async Task StateSurvivesApplicationRestartAndAnotherBootstrap()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        long orderId;
        string serializedBeforeRestart;
        await using (var app = await TestApplication.StartAsync(store))
        {
            using var created = await app.Client.PostAsJsonAsync(
                "/orders", new OrderRequest("durable", "SKU-1005", 3));
            Assert.Equal(HttpStatusCode.Created, created.StatusCode);
            orderId = (await created.Content.ReadFromJsonAsync<JsonElement>())
                .GetProperty("orderId").GetInt64();
            using var update = await app.Client.PutAsJsonAsync(
                $"/orders/{orderId}/quantity", new { quantity = 9 });
            Assert.Equal(HttpStatusCode.NoContent, update.StatusCode);
            serializedBeforeRestart =
                await app.Client.GetStringAsync($"/orders/{orderId}");
        }

        await store.BootstrapAsync();
        await using var restarted = await TestApplication.StartAsync(store);
        Assert.Equal(
            serializedBeforeRestart,
            await restarted.Client.GetStringAsync($"/orders/{orderId}"));
        var order = await restarted.Client.GetFromJsonAsync<OrderRecord>(
            $"/orders/{orderId}");
        Assert.Equal("durable", order!.CustomerId);
        Assert.Equal(9, order.Quantity);
        Assert.Equal(189m, order.UnitPrice);
    }

    [Theory]
    [InlineData("SKU-1001", "129.99")]
    [InlineData("SKU-1002", "349.00")]
    [InlineData("SKU-1003", "219.50")]
    [InlineData("SKU-1004", "45.75")]
    [InlineData("SKU-1005", "189.00")]
    [InlineData("sku-1001", "129.99")]
    public async Task UsesFixedLocalSamplePrices(string productId, string price)
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var response = await app.Client.PostAsJsonAsync(
            "/orders", new OrderRequest("customer", productId, 2));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(
            decimal.Parse(price, CultureInfo.InvariantCulture),
            body.GetProperty("unitPrice").GetDecimal());
        Assert.DoesNotContain(
            app.Channel.Items.OfType<DependencyTelemetry>(),
            item => item.Type == "Http");
    }

    public static IEnumerable<object[]> InvalidOrders()
    {
        yield return [new OrderRequest(null!, "SKU-1001", 1), "customerId"];
        yield return [new OrderRequest("", "SKU-1001", 1), "customerId"];
        yield return [new OrderRequest(" ", "SKU-1001", 1), "customerId"];
        yield return [
            new OrderRequest(new string('C', 65), "SKU-1001", 1), "customerId"];
        yield return [new OrderRequest("customer", null!, 1), "productId"];
        yield return [new OrderRequest("customer", " ", 1), "productId"];
        yield return [
            new OrderRequest("customer", new string('P', 65), 1), "productId"];
        yield return [new OrderRequest("customer", "SKU-9999", 1), "productId"];
        yield return [new OrderRequest("customer", "SKU-1001", 0), "quantity"];
        yield return [new OrderRequest("customer", "SKU-1001", -1), "quantity"];
        yield return [new OrderRequest("customer", "SKU-1001", 1001), "quantity"];
    }

    [Theory]
    [MemberData(nameof(InvalidOrders))]
    public async Task InvalidOrdersReturnProblemsWithoutWriting(
        OrderRequest request, string field)
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var response = await app.Client.PostAsJsonAsync("/orders", request);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.True(body.GetProperty("errors").TryGetProperty(field, out _));
        Assert.DoesNotContain(
            app.Channel.Items.OfType<DependencyTelemetry>(),
            item => item.Name == "Orders.Insert");
        Assert.Equal(
            5, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(1001)]
    public async Task InvalidQuantityUpdatesLeaveSeedOrdersUnchanged(int quantity)
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var response = await app.Client.PutAsJsonAsync(
            "/orders/-1/quantity", new { quantity });
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(
            1,
            (await app.Client.GetFromJsonAsync<OrderRecord>("/orders/-1"))!.Quantity);
    }

    [Fact]
    public async Task CustomerValuesAreStoredAsDataRatherThanSql()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        const string customer = "customer'); DROP TABLE orders; --";
        using var response = await app.Client.PostAsJsonAsync(
            "/orders", new OrderRequest(customer, "SKU-1001", 1));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        Assert.Equal(
            customer,
            (await app.Client.GetFromJsonAsync<OrderRecord>(
                response.Headers.Location))!.CustomerId);
        Assert.Equal(
            6, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Theory]
    [InlineData("GET", "/fault/status")]
    [InlineData("POST", "/fault/cpu")]
    [InlineData("POST", "/fault/postgresql")]
    [InlineData("POST", "/fault/reset")]
    public async Task FaultEndpointsDoNotExistOnThePublicApi(
        string method, string path)
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var request = new HttpRequestMessage(new HttpMethod(method), path)
        {
            Content = JsonContent.Create(new { })
        };
        using var response = await app.Client.SendAsync(request);
        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.DoesNotContain("/fault", await app.Client.GetStringAsync("/"));
    }

    [Fact]
    public async Task HealthDatabaseAndAvailabilityReflectPostgreSql()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var live = await app.Client.GetAsync("/health/live");
        using var ready = await app.Client.GetAsync("/health/ready");
        Assert.Equal(HttpStatusCode.OK, live.StatusCode);
        Assert.Equal(HttpStatusCode.OK, ready.StatusCode);
        Assert.Equal(
            "ready",
            (await ready.Content.ReadFromJsonAsync<JsonElement>())
                .GetProperty("status").GetString());
        var seeds = await app.Client.GetFromJsonAsync<JsonElement>("/orders");
        Assert.Equal(JsonValueKind.Array, seeds.ValueKind);
        Assert.Equal(5, seeds.GetArrayLength());
        foreach (var seed in seeds.EnumerateArray())
        {
            Assert.True(seed.GetProperty("orderId").TryGetInt64(out _));
            Assert.False(string.IsNullOrWhiteSpace(
                seed.GetProperty("customerId").GetString()));
            Assert.False(string.IsNullOrWhiteSpace(
                seed.GetProperty("productId").GetString()));
            Assert.True(seed.GetProperty("quantity").TryGetInt32(out _));
            Assert.True(seed.GetProperty("unitPrice").TryGetDecimal(out _));
            Assert.Equal(
                DateTimeKind.Utc,
                seed.GetProperty("createdUtc").GetDateTime().Kind);
        }
        var database =
            await app.Client.GetFromJsonAsync<DatabaseStatus>("/database");
        Assert.NotNull(database);
        Assert.Equal("PostgreSQL", database.Provider);
        Assert.Equal("ready", database.Status);
        Assert.Equal(
            DatabaseBootstrap.CurrentMigrationVersion, database.SchemaVersion);
        Assert.True(database.DatabaseBytes > 0);
        var availability =
            Assert.Single(app.Channel.Items.OfType<AvailabilityTelemetry>());
        Assert.True(availability.Success);
        Assert.Equal("orders-api", availability.Context.Cloud.RoleName);
    }

    [Fact]
    public async Task RuntimeStaysLiveWhenPostgreSqlSchemaIsUnavailable()
    {
        await using var store = await server.CreateDatabaseAsync();
        await using var app = await TestApplication.StartAsync(store);

        using var live = await app.Client.GetAsync("/health/live");
        using var ready = await app.Client.GetAsync("/health/ready");
        Assert.Equal(HttpStatusCode.OK, live.StatusCode);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, ready.StatusCode);
        Assert.Equal(0, await store.ExecuteScalarAsync<long>(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'orders';
            """));

        var availability =
            Assert.Single(app.Channel.Items.OfType<AvailabilityTelemetry>());
        Assert.False(availability.Success);
        Assert.Equal("orders-api", availability.Context.Cloud.RoleName);
        var dependency = Assert.Single(
            app.Channel.Items.OfType<DependencyTelemetry>(),
            item => item.Context.Operation.Id == availability.Context.Operation.Id);
        Assert.False(dependency.Success);
        Assert.Equal("PostgreSQL", dependency.Type);
        Assert.Equal("orders-api", dependency.Context.Cloud.RoleName);
        var exception = Assert.Single(
            app.Channel.Items.OfType<ExceptionTelemetry>(),
            item => item.Context.Operation.Id == availability.Context.Operation.Id);
        Assert.IsType<DatabaseOperationException>(exception.Exception);

        await store.BootstrapAsync();
        using var recovered = await app.Client.GetAsync("/health/ready");
        Assert.Equal(HttpStatusCode.OK, recovered.StatusCode);
        Assert.Equal(
            5, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Fact]
    public async Task PostgreSqlFailureAffectsBusinessAndReadinessButNotLiveness()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        await store.ExecuteAsync("DROP TABLE orders CASCADE;");

        using var live = await app.Client.GetAsync("/health/live");
        Assert.Equal(HttpStatusCode.OK, live.StatusCode);
        foreach (var path in new[] { "/health/ready", "/orders", "/orders/-1" })
        {
            using var response = await app.Client.GetAsync(path);
            Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
            var body = await response.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal(
                "Orders database unavailable",
                body.GetProperty("title").GetString());
            Assert.DoesNotContain(
                store.ConnectionString, body.ToString(), StringComparison.Ordinal);
        }
        using var created = await app.Client.PostAsJsonAsync(
            "/orders", new OrderRequest("customer", "SKU-1001", 1));
        using var updated = await app.Client.PutAsJsonAsync(
            "/orders/-1/quantity", new { quantity = 2 });
        Assert.Equal(HttpStatusCode.ServiceUnavailable, created.StatusCode);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, updated.StatusCode);
    }

    [Fact]
    public async Task PostgreSqlDependenciesCorrelateWithHttpRequests()
    {
        await using var store = await server.CreateDatabaseAsync();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var created = await app.Client.PostAsJsonAsync(
            "/orders", new OrderRequest("private-customer", "SKU-1001", 1));
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var successRequest = await app.Channel.WaitForAsync<RequestTelemetry>(
            item => item.Url.AbsolutePath == "/orders" && item.ResponseCode == "201");
        var successfulDependency = Assert.Single(
            app.Channel.Items.OfType<DependencyTelemetry>(),
            item => item.Name == "Orders.Insert");
        Assert.Equal(
            successRequest.Context.Operation.Id,
            successfulDependency.Context.Operation.Id);
        Assert.Equal(successRequest.Id, successfulDependency.Context.Operation.ParentId);
        Assert.Equal("PostgreSQL", successfulDependency.Type);
        Assert.True(successfulDependency.Success);
        Assert.Equal("0", successfulDependency.ResultCode);
        Assert.DoesNotContain("private-customer", successfulDependency.Data);

        await store.ExecuteAsync("DROP TABLE orders CASCADE;");
        using var failed = await app.Client.GetAsync("/orders");
        Assert.Equal(HttpStatusCode.ServiceUnavailable, failed.StatusCode);
        var failureRequest = await app.Channel.WaitForAsync<RequestTelemetry>(
            item => item.Url.AbsolutePath == "/orders" && item.ResponseCode == "503");
        var failedDependency = Assert.Single(
            app.Channel.Items.OfType<DependencyTelemetry>(),
            item => item.Name == "Orders.List");
        Assert.False(failureRequest.Success);
        Assert.False(failedDependency.Success);
        Assert.Equal("42P01", failedDependency.ResultCode);
        Assert.Equal(
            failureRequest.Context.Operation.Id,
            failedDependency.Context.Operation.Id);
        Assert.Equal(failureRequest.Id, failedDependency.Context.Operation.ParentId);
        var exception = Assert.Single(
            app.Channel.Items.OfType<ExceptionTelemetry>(),
            item => item.Properties.TryGetValue(
                "database.operation", out var operation)
                && operation == "Orders.List");
        Assert.IsType<DatabaseOperationException>(exception.Exception);
        Assert.Equal(
            failedDependency.Context.Operation.Id, exception.Context.Operation.Id);
        Assert.Equal(failedDependency.Id, exception.Context.Operation.ParentId);
    }

    [Fact]
    public async Task RuntimeRetainsDefaultAdaptiveSampling()
    {
        await using var store = await server.CreateDatabaseAsync();
        var builder = WebApplication.CreateBuilder(
            new WebApplicationOptions { ContentRootPath = AppContext.BaseDirectory });
        builder.Configuration.Sources.Clear();
        builder.Configuration.AddInMemoryCollection(
            new Dictionary<string, string?>
            {
                ["ConnectionStrings:OrdersDb"] = store.ConnectionString,
                ["OrdersDatabase:Authentication"] =
                    OrdersDatabase.PasswordAuthentication,
                ["APPLICATIONINSIGHTS_CONNECTION_STRING"] =
                    RecordingTelemetryChannel.ConnectionString
            });
        builder.Services.AddSingleton<ITelemetryChannel>(
            new RecordingTelemetryChannel());

        await using var app = Program.CreateApplication(builder);
        Assert.True(app.Services
            .GetRequiredService<IOptions<ApplicationInsightsServiceOptions>>()
            .Value.EnableAdaptiveSampling);
    }

    [Fact]
    public void RuntimeRequiresExplicitDatabaseConfiguration()
    {
        var builder = WebApplication.CreateBuilder(
            new WebApplicationOptions { ContentRootPath = AppContext.BaseDirectory });
        builder.Configuration.Sources.Clear();
        var exception = Assert.Throws<InvalidOperationException>(
            () => Program.CreateApplication(builder));
        Assert.Contains("ConnectionStrings__OrdersDb", exception.Message);
    }
}
