using System.Globalization;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.ApplicationInsights.AspNetCore.Extensions;
using Microsoft.ApplicationInsights.Channel;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.AspNetCore.Builder;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace OrdersApi.Tests;

public sealed class OrdersApiTests
{
    [Fact]
    public async Task CreateReadUpdateAndListOrders()
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        var customer = new string('C', 64);

        using var created = await app.Client.PostAsJsonAsync("/orders", new OrderRequest(customer, "SKU-1001", 1));
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var body = await created.Content.ReadFromJsonAsync<JsonElement>();
        var orderId = body.GetProperty("orderId").GetInt64();
        Assert.True(orderId > 0);
        Assert.Equal(129.99m, body.GetProperty("unitPrice").GetDecimal());
        Assert.Equal($"/orders/{orderId}", created.Headers.Location!.ToString());
        var order = await app.Client.GetFromJsonAsync<OrderRecord>(created.Headers.Location);
        Assert.Equal(customer, order!.CustomerId);
        Assert.Equal("SKU-1001", order.ProductId);
        Assert.Equal(1, order.Quantity);
        Assert.Equal(DateTimeKind.Utc, order.CreatedUtc.Kind);

        using var updated = await app.Client.PutAsJsonAsync($"/orders/{orderId}/quantity", new { quantity = 1000 });
        Assert.Equal(HttpStatusCode.NoContent, updated.StatusCode);
        order = await app.Client.GetFromJsonAsync<OrderRecord>($"/orders/{orderId}");
        Assert.Equal(1000, order!.Quantity);
        Assert.Equal(129.99m, order.UnitPrice);
        var orders = await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders");
        Assert.Equal(order, orders![0]);
        Assert.Equal(6, orders.Length);

        using var missing = await app.Client.GetAsync("/orders/999999");
        using var missingUpdate = await app.Client.PutAsJsonAsync("/orders/999999/quantity", new { quantity = 2 });
        Assert.Equal(HttpStatusCode.NotFound, missing.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, missingUpdate.StatusCode);
    }

    [Fact]
    public async Task StateSurvivesApplicationRestartAndAnotherBootstrap()
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        long orderId;
        string serializedBeforeRestart;
        await using (var app = await TestApplication.StartAsync(store))
        {
            using var created = await app.Client.PostAsJsonAsync("/orders", new OrderRequest("durable", "SKU-1005", 3));
            Assert.Equal(HttpStatusCode.Created, created.StatusCode);
            orderId = (await created.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("orderId").GetInt64();
            using var update = await app.Client.PutAsJsonAsync($"/orders/{orderId}/quantity", new { quantity = 9 });
            Assert.Equal(HttpStatusCode.NoContent, update.StatusCode);
            serializedBeforeRestart = await app.Client.GetStringAsync($"/orders/{orderId}");
        }
        await store.BootstrapAsync();
        await using var restarted = await TestApplication.StartAsync(store);
        Assert.Equal(serializedBeforeRestart, await restarted.Client.GetStringAsync($"/orders/{orderId}"));
        var order = await restarted.Client.GetFromJsonAsync<OrderRecord>($"/orders/{orderId}");
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
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var response = await app.Client.PostAsJsonAsync("/orders", new OrderRequest("customer", productId, 2));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(decimal.Parse(price, CultureInfo.InvariantCulture), body.GetProperty("unitPrice").GetDecimal());
        Assert.DoesNotContain(app.Channel.Items.OfType<DependencyTelemetry>(), item => item.Type == "Http");
    }

    public static IEnumerable<object[]> InvalidOrders()
    {
        yield return [new OrderRequest(null!, "SKU-1001", 1), "customerId"];
        yield return [new OrderRequest("", "SKU-1001", 1), "customerId"];
        yield return [new OrderRequest(" ", "SKU-1001", 1), "customerId"];
        yield return [new OrderRequest(new string('C', 65), "SKU-1001", 1), "customerId"];
        yield return [new OrderRequest("customer", null!, 1), "productId"];
        yield return [new OrderRequest("customer", " ", 1), "productId"];
        yield return [new OrderRequest("customer", new string('P', 65), 1), "productId"];
        yield return [new OrderRequest("customer", "SKU-9999", 1), "productId"];
        yield return [new OrderRequest("customer", "SKU-1001", 0), "quantity"];
        yield return [new OrderRequest("customer", "SKU-1001", -1), "quantity"];
        yield return [new OrderRequest("customer", "SKU-1001", 1001), "quantity"];
    }

    [Theory]
    [MemberData(nameof(InvalidOrders))]
    public async Task InvalidOrdersReturnValidationProblemsWithoutWriting(OrderRequest request, string field)
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var response = await app.Client.PostAsJsonAsync("/orders", request);
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.True(body.GetProperty("errors").TryGetProperty(field, out _));
        Assert.DoesNotContain(app.Channel.Items.OfType<DependencyTelemetry>(), item => item.Name == "Orders.Insert");
        Assert.Equal(5, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    [InlineData(1001)]
    public async Task InvalidQuantityUpdatesLeaveSeedOrdersUnchanged(int quantity)
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var response = await app.Client.PutAsJsonAsync("/orders/-1/quantity", new { quantity });
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(1, (await app.Client.GetFromJsonAsync<OrderRecord>("/orders/-1"))!.Quantity);
    }

    [Fact]
    public async Task CustomerValuesAreStoredAsDataRatherThanSql()
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        const string customer = "customer'); DROP TABLE Orders; --";
        using var response = await app.Client.PostAsJsonAsync("/orders", new OrderRequest(customer, "SKU-1001", 1));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        Assert.Equal(customer, (await app.Client.GetFromJsonAsync<OrderRecord>(response.Headers.Location))!.CustomerId);
        Assert.Equal(6, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Theory]
    [InlineData("GET", "/fault/status")]
    [InlineData("POST", "/fault/cpu")]
    [InlineData("POST", "/fault/errors")]
    [InlineData("POST", "/fault/storage")]
    [InlineData("POST", "/fault/storage/release")]
    [InlineData("POST", "/fault/reset")]
    public async Task DestructiveFaultEndpointsDoNotExistEvenWithLegacyFlags(string method, string path)
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var request = new HttpRequestMessage(new HttpMethod(method), path)
        {
            Content = JsonContent.Create(new { })
        };
        request.Headers.Add("X-Fault-Token", "obsolete-token");
        using var response = await app.Client.SendAsync(request);
        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.DoesNotContain("/fault", await app.Client.GetStringAsync("/"));
    }

    [Fact]
    public async Task HealthStorageAndAvailabilityReflectTheConfiguredDatabase()
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var live = await app.Client.GetAsync("/health/live");
        using var ready = await app.Client.GetAsync("/health/ready");
        Assert.Equal(HttpStatusCode.OK, live.StatusCode);
        Assert.Equal(HttpStatusCode.OK, ready.StatusCode);
        Assert.Equal("ready", (await ready.Content.ReadFromJsonAsync<JsonElement>()).GetProperty("status").GetString());
        var seeds = await app.Client.GetFromJsonAsync<JsonElement>("/orders");
        Assert.Equal(JsonValueKind.Array, seeds.ValueKind);
        Assert.Equal(5, seeds.GetArrayLength());
        foreach (var seed in seeds.EnumerateArray())
        {
            Assert.True(seed.GetProperty("orderId").TryGetInt64(out _));
            Assert.False(string.IsNullOrWhiteSpace(seed.GetProperty("customerId").GetString()));
            Assert.False(string.IsNullOrWhiteSpace(seed.GetProperty("productId").GetString()));
            Assert.True(seed.GetProperty("quantity").TryGetInt32(out _));
            Assert.True(seed.GetProperty("unitPrice").TryGetDecimal(out _));
            Assert.Equal(DateTimeKind.Utc, seed.GetProperty("createdUtc").GetDateTime().Kind);
        }
        var storage = await app.Client.GetFromJsonAsync<StorageUsage>("/storage");
        Assert.NotNull(storage);
        Assert.True(storage.MaxBytes > 0);
        Assert.True(storage.DatabaseBytes > 0);
        Assert.InRange(storage.AvailableBytes, 0, storage.MaxBytes);
        Assert.Equal(storage.MaxBytes, storage.UsedBytes + storage.AvailableBytes);
        Assert.InRange(storage.UsedPercent, 0, 100);
        var availability = Assert.Single(app.Channel.Items.OfType<AvailabilityTelemetry>());
        Assert.True(availability.Success);
        Assert.Equal("orders-api", availability.Context.Cloud.RoleName);
    }

    [Fact]
    public async Task RuntimeStartsWithUnavailableSqliteAndReportsFailureWithoutCreatingTheDatabase()
    {
        using var store = new TestDatabase();
        await using var app = await TestApplication.StartAsync(store);

        using var live = await app.Client.GetAsync("/health/live");
        using var ready = await app.Client.GetAsync("/health/ready");
        Assert.Equal(HttpStatusCode.OK, live.StatusCode);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, ready.StatusCode);
        Assert.False(File.Exists(store.Database.DataSource));

        var availability = Assert.Single(app.Channel.Items.OfType<AvailabilityTelemetry>());
        Assert.False(availability.Success);
        Assert.Equal("orders-api", availability.Context.Cloud.RoleName);
        var dependency = Assert.Single(app.Channel.Items.OfType<DependencyTelemetry>(), item =>
            item.Context.Operation.Id == availability.Context.Operation.Id);
        Assert.False(dependency.Success);
        Assert.Equal("SQLite", dependency.Type);
        Assert.Equal("orders-api", dependency.Context.Cloud.RoleName);
        var exception = Assert.Single(app.Channel.Items.OfType<ExceptionTelemetry>(), item =>
            item.Context.Operation.Id == availability.Context.Operation.Id);
        Assert.Equal(14, Assert.IsType<SqliteException>(exception.Exception).SqliteErrorCode);
        Assert.Equal("orders-api", exception.Context.Cloud.RoleName);

        await store.BootstrapAsync();
        using var recovered = await app.Client.GetAsync("/health/ready");
        Assert.Equal(HttpStatusCode.OK, recovered.StatusCode);
        Assert.Equal(5, (await app.Client.GetFromJsonAsync<OrderRecord[]>("/orders"))!.Length);
    }

    [Fact]
    public async Task SqliteFailureMakesReadinessAndBusinessRoutesUnavailableButNotLiveness()
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        await store.ExecuteAsync("DROP TABLE Orders;");

        using var live = await app.Client.GetAsync("/health/live");
        Assert.Equal(HttpStatusCode.OK, live.StatusCode);
        foreach (var path in new[] { "/health/ready", "/orders", "/orders/-1" })
        {
            using var response = await app.Client.GetAsync(path);
            Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
            var body = await response.Content.ReadFromJsonAsync<JsonElement>();
            Assert.Equal("Orders database unavailable", body.GetProperty("title").GetString());
            Assert.DoesNotContain(store.Database.DataSource, body.ToString());
        }
        using var created = await app.Client.PostAsJsonAsync("/orders", new OrderRequest("customer", "SKU-1001", 1));
        using var updated = await app.Client.PutAsJsonAsync("/orders/-1/quantity", new { quantity = 2 });
        Assert.Equal(HttpStatusCode.ServiceUnavailable, created.StatusCode);
        Assert.Equal(HttpStatusCode.ServiceUnavailable, updated.StatusCode);
    }

    [Fact]
    public async Task SqliteDependenciesAndExceptionsCorrelateWithHttpRequests()
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var app = await TestApplication.StartAsync(store);
        using var created = await app.Client.PostAsJsonAsync("/orders", new OrderRequest("private-customer", "SKU-1001", 1));
        Assert.Equal(HttpStatusCode.Created, created.StatusCode);
        var successRequest = await app.Channel.WaitForAsync<RequestTelemetry>(item =>
            item.Url.AbsolutePath == "/orders" && item.ResponseCode == "201");
        var successfulDependency = Assert.Single(app.Channel.Items.OfType<DependencyTelemetry>(), item =>
            item.Name == "Orders.Insert");
        Assert.Equal(successRequest.Context.Operation.Id, successfulDependency.Context.Operation.Id);
        Assert.Equal(successRequest.Id, successfulDependency.Context.Operation.ParentId);
        Assert.Equal("SQLite", successfulDependency.Type);
        Assert.True(successfulDependency.Success);
        Assert.Equal("0", successfulDependency.ResultCode);
        Assert.DoesNotContain("private-customer", successfulDependency.Data);
        Assert.Equal("orders-api", successRequest.Context.Cloud.RoleName);
        Assert.Equal("orders-api", successfulDependency.Context.Cloud.RoleName);

        await store.ExecuteAsync("DROP TABLE Orders;");
        using var failed = await app.Client.GetAsync("/orders");
        Assert.Equal(HttpStatusCode.ServiceUnavailable, failed.StatusCode);
        var failureRequest = await app.Channel.WaitForAsync<RequestTelemetry>(item =>
            item.Url.AbsolutePath == "/orders" && item.ResponseCode == "503");
        var failedDependency = Assert.Single(app.Channel.Items.OfType<DependencyTelemetry>(), item =>
            item.Name == "Orders.List");
        Assert.False(failureRequest.Success);
        Assert.False(failedDependency.Success);
        Assert.Equal("1", failedDependency.ResultCode);
        Assert.Equal(failureRequest.Context.Operation.Id, failedDependency.Context.Operation.Id);
        Assert.Equal(failureRequest.Id, failedDependency.Context.Operation.ParentId);
        var exception = Assert.Single(app.Channel.Items.OfType<ExceptionTelemetry>(), item =>
            item.Properties.TryGetValue("database.operation", out var operation) && operation == "Orders.List");
        Assert.IsType<SqliteException>(exception.Exception);
        Assert.Equal(failedDependency.Context.Operation.Id, exception.Context.Operation.Id);
        Assert.Equal(failedDependency.Id, exception.Context.Operation.ParentId);
        Assert.Equal("orders-api", exception.Context.Cloud.RoleName);
    }

    [Fact]
    public async Task RuntimeRetainsDefaultAdaptiveSampling()
    {
        using var store = new TestDatabase();
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions { ContentRootPath = AppContext.BaseDirectory });
        builder.Configuration.Sources.Clear();
        builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["ConnectionStrings:OrdersDb"] = store.ConnectionString,
            ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = RecordingTelemetryChannel.ConnectionString
        });
        builder.Services.AddSingleton<ITelemetryChannel>(new RecordingTelemetryChannel());

        await using var app = Program.CreateApplication(builder);
        Assert.True(app.Services.GetRequiredService<IOptions<ApplicationInsightsServiceOptions>>().Value.EnableAdaptiveSampling);
    }

    [Fact]
    public void RuntimeRequiresExplicitDatabaseConfiguration()
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions { ContentRootPath = AppContext.BaseDirectory });
        builder.Configuration.Sources.Clear();
        var exception = Assert.Throws<InvalidOperationException>(() => Program.CreateApplication(builder));
        Assert.Contains("ConnectionStrings__OrdersDb", exception.Message);
    }
}
