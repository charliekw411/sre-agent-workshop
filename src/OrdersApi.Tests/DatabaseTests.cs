using System.Diagnostics;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.Data.Sqlite;

namespace OrdersApi.Tests;

public sealed class DatabaseTests
{
    [Fact]
    public async Task BootstrapIsDeterministicAndIdempotent()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        var first = await fixture.Repository.GetRecentOrdersAsync(25, CancellationToken.None);
        await fixture.Store.BootstrapAsync();
        var second = await fixture.Repository.GetRecentOrdersAsync(25, CancellationToken.None);

        Assert.Equal(first, second);
        Assert.Equal(5, second.Count);
        decimal[] prices = [129.99m, 349.00m, 219.50m, 45.75m, 189.00m];
        for (var index = 0; index < second.Count; index++)
        {
            Assert.Equal(-(index + 1), second[index].OrderId);
            Assert.Equal($"SKU-{1001 + index}", second[index].ProductId);
            Assert.Equal(prices[index], second[index].UnitPrice);
            Assert.Equal("workshop-seed", second[index].CustomerId);
            Assert.Equal(1, second[index].Quantity);
            Assert.Equal(new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc), second[index].CreatedUtc);
            Assert.Equal(DateTimeKind.Utc, second[index].CreatedUtc.Kind);
        }
    }

    [Fact]
    public async Task BootstrapPreservesExistingRowsAndDoesNotRewindPositiveIds()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        await fixture.Repository.UpdateQuantityAsync(-1, 37, CancellationToken.None);
        var id = await fixture.Repository.CreateOrderAsync(new("customer", "SKU-1002", 4), 349m, CancellationToken.None);
        var before = await fixture.Repository.GetOrderAsync(id, CancellationToken.None);
        Assert.True(id > 0);

        await fixture.Store.ExecuteAsync("""
            INSERT INTO Orders (OrderId, CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc)
            VALUES (1000, 'existing', 'SKU-1005', 12, '189.00', '2026-02-01T00:00:00.0000000Z');
            DELETE FROM Orders WHERE OrderId = -3;
            """);
        await fixture.Store.BootstrapAsync();

        Assert.Equal(before, await fixture.Repository.GetOrderAsync(id, CancellationToken.None));
        Assert.Equal(37, (await fixture.Repository.GetOrderAsync(-1, CancellationToken.None))!.Quantity);
        Assert.Equal(12, (await fixture.Repository.GetOrderAsync(1000, CancellationToken.None))!.Quantity);
        Assert.Equal("SKU-1003", (await fixture.Repository.GetOrderAsync(-3, CancellationToken.None))!.ProductId);
        await fixture.Store.ExecuteAsync("DELETE FROM Orders WHERE OrderId = 1000;");
        await fixture.Store.BootstrapAsync();
        var nextId = await fixture.Repository.CreateOrderAsync(new("next", "SKU-1001", 1), 129.99m, CancellationToken.None);
        Assert.True(nextId > 1000);
    }

    [Fact]
    public async Task BootstrapRollsBackPartialSeedingOnSqliteFailure()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        var id = await fixture.Repository.CreateOrderAsync(new("preserve", "SKU-1001", 2), 129.99m, CancellationToken.None);
        await fixture.Store.ExecuteAsync("""
            DELETE FROM Orders WHERE OrderId < 0;
            CREATE TRIGGER RejectThirdSeed BEFORE INSERT ON Orders
            WHEN NEW.OrderId = -3 BEGIN SELECT RAISE(ABORT, 'seed blocked'); END;
            """);

        await Assert.ThrowsAsync<SqliteException>(() => fixture.Store.BootstrapAsync());
        var orders = await fixture.Repository.GetRecentOrdersAsync(25, CancellationToken.None);
        Assert.Equal(id, Assert.Single(orders).OrderId);
    }

    [Fact]
    public async Task BootstrapCommandAcceptsDeploymentConnectionString()
    {
        using var store = new TestDatabase();
        var args = new[] { "--ConnectionStrings:OrdersDb", store.ConnectionString };
        Assert.Equal(0, await DatabaseBootstrap.RunAsync(args));
        Assert.Equal(0, await DatabaseBootstrap.RunAsync(args));
        Assert.True(File.Exists(store.Database.DataSource));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData(" ")]
    [InlineData("Server=old-sql-host;Database=Orders")]
    [InlineData("Data Source=:memory:")]
    [InlineData("Data Source=relative.db")]
    [InlineData("Data Source=file:orders.db?mode=memory")]
    public void InvalidConfigurationFailsClearly(string? connectionString)
    {
        var exception = Assert.Throws<InvalidOperationException>(() => new OrdersDatabase(connectionString));
        Assert.Contains("ConnectionStrings__OrdersDb", exception.Message);
    }

    [Theory]
    [InlineData("Mode=ReadOnly")]
    [InlineData("Mode=Memory")]
    [InlineData("Password=unsupported")]
    public void NonWritableOrEncryptedConfigurationIsRejected(string option)
    {
        using var store = new TestDatabase();
        Assert.Throws<InvalidOperationException>(() => new OrdersDatabase($"{store.ConnectionString};{option}"));
    }

    [Fact]
    public async Task BootstrapEnablesWalAndConnectionsHaveBoundedTimeouts()
    {
        using var store = new TestDatabase();
        await store.BootstrapAsync();
        await using var connection = store.Database.CreateConnection();
        await connection.OpenAsync();
        await OrdersDatabase.ConfigureConnectionAsync(connection, CancellationToken.None);
        Assert.Equal(OrdersDatabase.BusyTimeoutSeconds, connection.DefaultTimeout);
        await using var command = connection.CreateCommand();
        command.CommandText = "PRAGMA journal_mode;";
        Assert.Equal("wal", await command.ExecuteScalarAsync());
        command.CommandText = "PRAGMA busy_timeout;";
        Assert.Equal(5000L, await command.ExecuteScalarAsync());
    }

    [Fact]
    public async Task RuntimeDoesNotSilentlyCreateAnUnbootstrappedDatabase()
    {
        using var fixture = new RepositoryFixture();
        var exception = await Assert.ThrowsAsync<SqliteException>(() => fixture.Repository.ProbeAsync(CancellationToken.None));
        Assert.Equal(14, exception.SqliteErrorCode);
        Assert.False(File.Exists(fixture.Store.Database.DataSource));
    }

    [Fact]
    public async Task ConcurrentWritesUseDistinctPositiveIdsAndPreserveExactPrices()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        var ids = await Task.WhenAll(Enumerable.Range(0, 30).Select(index => Task.Run(() =>
            fixture.Repository.CreateOrderAsync(new($"customer-{index}", "SKU-1001", 1), 129.99m, CancellationToken.None))));

        Assert.Equal(30, ids.Distinct().Count());
        Assert.All(ids, id => Assert.True(id > 0));
        var orders = await fixture.Repository.GetRecentOrdersAsync(25, CancellationToken.None);
        Assert.Equal(25, orders.Count);
        Assert.All(orders, order => Assert.Equal(129.99m, order.UnitPrice));
    }

    [Fact]
    public async Task IdempotentCreateSurvivesBootstrapAndRejectsAnotherPayload()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        var request = new OrderRequest("retry-customer", "SKU-1003", 4);

        var created = await fixture.Repository.CreateOrderIdempotentlyAsync(
            request, 219.50m, "persisted-request", CancellationToken.None);
        await fixture.Store.BootstrapAsync();
        var replayed = await fixture.Repository.CreateOrderIdempotentlyAsync(
            request, 219.50m, "persisted-request", CancellationToken.None);
        var conflict = await fixture.Repository.CreateOrderIdempotentlyAsync(
            request with { Quantity = 5 }, 219.50m, "persisted-request", CancellationToken.None);

        Assert.NotNull(created);
        Assert.NotNull(replayed);
        Assert.False(created.Replayed);
        Assert.True(replayed.Replayed);
        Assert.Equal(created.OrderId, replayed.OrderId);
        Assert.Equal(219.50m, replayed.UnitPrice);
        Assert.Null(conflict);
        Assert.Equal(6, (await fixture.Repository.GetRecentOrdersAsync(25, CancellationToken.None)).Count);
    }

    [Fact]
    public async Task ConcurrentRetriesCreateExactlyOneOrder()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        var request = new OrderRequest("concurrent-retry", "SKU-1004", 3);

        var attempts = await Task.WhenAll(Enumerable.Range(0, 8).Select(_ => Task.Run(() =>
            fixture.Repository.CreateOrderIdempotentlyAsync(
                request, 45.75m, "concurrent-request", CancellationToken.None))));

        Assert.All(attempts, Assert.NotNull);
        Assert.Single(attempts, result => !result!.Replayed);
        Assert.Single(attempts.Select(result => result!.OrderId).Distinct());
        Assert.Equal(6, (await fixture.Repository.GetRecentOrdersAsync(25, CancellationToken.None)).Count);
    }

    [Fact]
    public async Task LockedWriterFailsWithinTheBusyTimeoutAndEmitsFailureTelemetry()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        await using var blockingConnection = fixture.Store.Database.CreateConnection();
        await blockingConnection.OpenAsync();
        await using var transaction = blockingConnection.BeginTransaction();

        var stopwatch = Stopwatch.StartNew();
        var exception = await Assert.ThrowsAsync<SqliteException>(() =>
            fixture.Repository.CreateOrderAsync(new("blocked", "SKU-1001", 1), 129.99m, CancellationToken.None));
        Assert.Equal(5, exception.SqliteErrorCode);
        Assert.InRange(stopwatch.Elapsed, TimeSpan.FromSeconds(4), TimeSpan.FromSeconds(15));
        var dependency = Assert.Single(fixture.Channel.Items.OfType<DependencyTelemetry>());
        Assert.False(dependency.Success);
        Assert.Equal("5", dependency.ResultCode);
        Assert.IsType<SqliteException>(Assert.Single(fixture.Channel.Items.OfType<ExceptionTelemetry>()).Exception);
    }

    [Fact]
    public async Task AvailabilityReportsBothSqliteSuccessAndFailureWithCorrelation()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        var service = new DatabaseAvailabilityService(fixture.Repository, fixture.Telemetry);
        await service.CheckAsync(CancellationToken.None);
        await fixture.Store.ExecuteAsync("DROP TABLE Orders;");
        await service.CheckAsync(CancellationToken.None);

        var samples = fixture.Channel.Items.OfType<AvailabilityTelemetry>().ToArray();
        Assert.Equal(2, samples.Length);
        Assert.True(samples[0].Success);
        Assert.False(samples[1].Success);
        Assert.Contains("SQLite error", samples[1].Message);
        Assert.All(samples, sample =>
        {
            Assert.Equal("orders-api-sqlite", sample.Name);
            Assert.Equal("orders-api", sample.Context.Cloud.RoleName);
            Assert.NotEmpty(sample.RunLocation);
            var dependency = Assert.Single(fixture.Channel.Items.OfType<DependencyTelemetry>(),
                dependency => dependency.Context.Operation.Id == sample.Context.Operation.Id);
            Assert.Equal(sample.Id, dependency.Context.Operation.ParentId);
            Assert.Equal(sample.Success, dependency.Success);
        });
    }

    [Fact]
    public async Task CancellationIsRecordedButNotReportedAsASqliteException()
    {
        using var fixture = new RepositoryFixture();
        await fixture.Store.BootstrapAsync();
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => fixture.Repository.ProbeAsync(cancellation.Token));
        var dependency = Assert.Single(fixture.Channel.Items.OfType<DependencyTelemetry>());
        Assert.False(dependency.Success);
        Assert.Equal("Cancelled", dependency.ResultCode);
        Assert.Empty(fixture.Channel.Items.OfType<ExceptionTelemetry>());
    }
}
