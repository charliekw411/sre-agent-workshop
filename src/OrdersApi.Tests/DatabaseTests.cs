using System.Diagnostics;
using Microsoft.ApplicationInsights.DataContracts;
using Npgsql;

namespace OrdersApi.Tests;

[Collection(PostgreSqlCollection.Name)]
public sealed class DatabaseTests(PostgreSqlServerFixture server)
{
    [Fact]
    public async Task BootstrapIsDeterministicAndIdempotent()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        var first = await fixture.Repository.GetRecentOrdersAsync(
            25, CancellationToken.None);
        await fixture.Store.BootstrapAsync();
        var second = await fixture.Repository.GetRecentOrdersAsync(
            25, CancellationToken.None);

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
            Assert.Equal(
                new DateTime(2026, 1, 1, 0, 0, 0, DateTimeKind.Utc),
                second[index].CreatedUtc);
            Assert.Equal(DateTimeKind.Utc, second[index].CreatedUtc.Kind);
        }
        Assert.Equal(
            DatabaseBootstrap.CurrentMigrationVersion,
            await fixture.Store.ExecuteScalarAsync<long>(
                "SELECT MAX(version) FROM orders_schema_migrations;"));
        Assert.Equal(
            1,
            await fixture.Repository.CreateOrderAsync(
                new("first-positive", "SKU-1001", 1),
                129.99m,
                CancellationToken.None));
    }

    [Fact]
    public async Task ConcurrentBootstrapsSerializeBeforeTakingMigrationSnapshots()
    {
        await using var store = await server.CreateDatabaseAsync();

        await Task.WhenAll(Enumerable.Range(0, 8).Select(_ =>
            DatabaseBootstrap.InitializeAsync(
                store.Database, CancellationToken.None)));

        Assert.Equal(1, await store.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM orders_schema_migrations;"));
        Assert.Equal(5, await store.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM orders WHERE order_id < 0;"));
    }

    [Fact]
    public async Task BootstrapPreservesRowsAndSynchronizesThePositiveIdentity()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        await fixture.Repository.UpdateQuantityAsync(-1, 37, CancellationToken.None);
        var id = await fixture.Repository.CreateOrderAsync(
            new("customer", "SKU-1002", 4), 349m, CancellationToken.None);
        var before = await fixture.Repository.GetOrderAsync(id, CancellationToken.None);
        Assert.Equal(1, id);

        await fixture.Store.ExecuteAsync("""
            INSERT INTO orders
                (order_id, customer_id, product_id, quantity, unit_price, created_utc)
            VALUES
                (1000, 'existing', 'SKU-1005', 12, 189.00, '2026-02-01T00:00:00Z');
            DELETE FROM orders WHERE order_id = -3;
            """);
        await fixture.Store.BootstrapAsync();

        Assert.Equal(before, await fixture.Repository.GetOrderAsync(
            id, CancellationToken.None));
        Assert.Equal(37, (await fixture.Repository.GetOrderAsync(
            -1, CancellationToken.None))!.Quantity);
        Assert.Equal(12, (await fixture.Repository.GetOrderAsync(
            1000, CancellationToken.None))!.Quantity);
        Assert.Equal("SKU-1003", (await fixture.Repository.GetOrderAsync(
            -3, CancellationToken.None))!.ProductId);
        var nextId = await fixture.Repository.CreateOrderAsync(
            new("next", "SKU-1001", 1), 129.99m, CancellationToken.None);
        Assert.True(nextId > 1000);
        await fixture.Store.ExecuteAsync(
            $"DELETE FROM orders WHERE order_id = {nextId};");
        await fixture.Store.BootstrapAsync();
        var idAfterGap = await fixture.Repository.CreateOrderAsync(
            new("after-gap", "SKU-1001", 1), 129.99m, CancellationToken.None);
        Assert.True(idAfterGap > nextId);
    }

    [Fact]
    public async Task BootstrapRollsBackPartialSeedingOnPostgreSqlFailure()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        var id = await fixture.Repository.CreateOrderAsync(
            new("preserve", "SKU-1001", 2), 129.99m, CancellationToken.None);
        await fixture.Store.ExecuteAsync("""
            DELETE FROM orders WHERE order_id < 0;

            CREATE FUNCTION reject_third_seed() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.order_id = -3 THEN
                    RAISE EXCEPTION 'seed blocked';
                END IF;
                RETURN NEW;
            END;
            $$;

            CREATE TRIGGER reject_third_seed
            BEFORE INSERT ON orders
            FOR EACH ROW EXECUTE FUNCTION reject_third_seed();
            """);

        await Assert.ThrowsAsync<PostgresException>(() =>
            fixture.Store.BootstrapAsync());
        var orders = await fixture.Repository.GetRecentOrdersAsync(
            25, CancellationToken.None);
        Assert.Equal(id, Assert.Single(orders).OrderId);
    }

    [Fact]
    public async Task BootstrapCommandAcceptsDeploymentConfiguration()
    {
        await using var store = await server.CreateDatabaseAsync();
        var args = new[]
        {
            "--ConnectionStrings:OrdersDb", store.ConnectionString,
            "--OrdersDatabase:Authentication", OrdersDatabase.PasswordAuthentication
        };

        Assert.Equal(0, await DatabaseBootstrap.RunAsync(args));
        Assert.Equal(0, await DatabaseBootstrap.RunAsync(args));
        Assert.Equal(5, await store.ExecuteScalarAsync<long>(
            "SELECT COUNT(*) FROM orders;"));
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData(" ")]
    [InlineData("Host=postgres;Database=orders")]
    [InlineData("Host=postgres;Username=orders;Password=test")]
    [InlineData("Database=orders;Username=orders;Password=test")]
    public void InvalidConfigurationFailsClearly(string? connectionString)
    {
        var exception = Assert.Throws<InvalidOperationException>(() =>
            new OrdersDatabase(
                connectionString, OrdersDatabase.PasswordAuthentication));
        Assert.Contains("ConnectionStrings__OrdersDb", exception.Message);
    }

    [Fact]
    public async Task AuthenticationModesAreValidatedWithoutAcquiringAToken()
    {
        await using var store = await server.CreateDatabaseAsync();
        var password = new NpgsqlConnectionStringBuilder(store.ConnectionString);

        Assert.Throws<InvalidOperationException>(() =>
            new OrdersDatabase(password.ConnectionString, "Unknown"));

        var noPassword = new NpgsqlConnectionStringBuilder(password.ConnectionString)
        {
            Password = "",
            SslMode = SslMode.Require
        };
        Assert.Throws<InvalidOperationException>(() =>
            new OrdersDatabase(
                noPassword.ConnectionString,
                OrdersDatabase.ManagedIdentityAuthentication));

        noPassword.SslMode = SslMode.VerifyFull;
        await using var managed = new OrdersDatabase(
            noPassword.ConnectionString,
            OrdersDatabase.ManagedIdentityAuthentication);
        Assert.Throws<InvalidOperationException>(() =>
            new OrdersDatabase(
                noPassword.ConnectionString,
                OrdersDatabase.ManagedIdentityAuthentication,
                "not-a-client-id"));

        Assert.Throws<InvalidOperationException>(() =>
            new OrdersDatabase(
                password.ConnectionString,
                OrdersDatabase.ManagedIdentityAuthentication));
    }

    [Fact]
    public async Task ConnectionsArePooledAndHaveBoundedTimeouts()
    {
        await using var store = await server.CreateDatabaseAsync();
        await using var connection = store.Database.CreateConnection();

        Assert.Equal(OrdersDatabase.CommandTimeoutSeconds, connection.ConnectionTimeout);
        Assert.Equal(OrdersDatabase.CommandTimeoutSeconds, connection.CommandTimeout);
        Assert.True(new NpgsqlConnectionStringBuilder(connection.ConnectionString).Pooling);
    }

    [Fact]
    public async Task RuntimeDoesNotCreateAnUnbootstrappedSchema()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);

        await Assert.ThrowsAsync<PostgresException>(() =>
            fixture.Repository.ProbeAsync(CancellationToken.None));
        Assert.Equal(0, await fixture.Store.ExecuteScalarAsync<long>(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'orders';
            """));
    }

    [Fact]
    public async Task ConcurrentWritesUseDistinctPositiveIdsAndExactPrices()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        var ids = await Task.WhenAll(Enumerable.Range(0, 30).Select(index =>
            fixture.Repository.CreateOrderAsync(
                new($"customer-{index}", "SKU-1001", 1),
                129.99m,
                CancellationToken.None)));

        Assert.Equal(30, ids.Distinct().Count());
        Assert.All(ids, id => Assert.True(id > 0));
        var orders = await fixture.Repository.GetRecentOrdersAsync(
            25, CancellationToken.None);
        Assert.Equal(25, orders.Count);
        Assert.All(orders, order => Assert.Equal(129.99m, order.UnitPrice));
    }

    [Fact]
    public async Task IdempotentCreateSurvivesBootstrapAndRejectsAnotherPayload()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        var request = new OrderRequest("retry-customer", "SKU-1003", 4);

        var created = await fixture.Repository.CreateOrderIdempotentlyAsync(
            request, 219.50m, "persisted-request", CancellationToken.None);
        await fixture.Store.BootstrapAsync();
        var replayed = await fixture.Repository.CreateOrderIdempotentlyAsync(
            request, 219.50m, "persisted-request", CancellationToken.None);
        var conflict = await fixture.Repository.CreateOrderIdempotentlyAsync(
            request with { Quantity = 5 },
            219.50m,
            "persisted-request",
            CancellationToken.None);

        Assert.NotNull(created);
        Assert.NotNull(replayed);
        Assert.False(created.Replayed);
        Assert.True(replayed.Replayed);
        Assert.Equal(created.OrderId, replayed.OrderId);
        Assert.Equal(219.50m, replayed.UnitPrice);
        Assert.Null(conflict);
        Assert.Equal(6, (await fixture.Repository.GetRecentOrdersAsync(
            25, CancellationToken.None)).Count);
    }

    [Fact]
    public async Task ConcurrentRetriesCreateExactlyOneOrder()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        var request = new OrderRequest("concurrent-retry", "SKU-1004", 3);

        var attempts = await Task.WhenAll(Enumerable.Range(0, 8).Select(_ =>
            fixture.Repository.CreateOrderIdempotentlyAsync(
                request, 45.75m, "concurrent-request", CancellationToken.None)));

        Assert.All(attempts, Assert.NotNull);
        Assert.Single(attempts, result => !result!.Replayed);
        Assert.Single(attempts.Select(result => result!.OrderId).Distinct());
        Assert.Equal(6, (await fixture.Repository.GetRecentOrdersAsync(
            25, CancellationToken.None)).Count);
    }

    [Fact]
    public async Task LockedWriterTimesOutAndEmitsSanitizedFailureTelemetry()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        await using var blockingConnection =
            await fixture.Store.Database.OpenConnectionAsync();
        await using var transaction = await blockingConnection.BeginTransactionAsync();
        await using (var command = blockingConnection.CreateCommand())
        {
            command.Transaction = transaction;
            command.CommandText = "LOCK TABLE orders IN ACCESS EXCLUSIVE MODE;";
            await command.ExecuteNonQueryAsync();
        }

        var stopwatch = Stopwatch.StartNew();
        var exception = await Assert.ThrowsAnyAsync<NpgsqlException>(() =>
            fixture.Repository.CreateOrderAsync(
                new("blocked", "SKU-1001", 1),
                129.99m,
                CancellationToken.None));
        Assert.InRange(
            stopwatch.Elapsed, TimeSpan.FromSeconds(4), TimeSpan.FromSeconds(15));
        var dependency = Assert.Single(
            fixture.Channel.Items.OfType<DependencyTelemetry>());
        Assert.False(dependency.Success);
        Assert.NotEqual("0", dependency.ResultCode);
        var recorded = Assert.IsType<DatabaseOperationException>(
            Assert.Single(
                fixture.Channel.Items.OfType<ExceptionTelemetry>()).Exception);
        Assert.DoesNotContain(exception.Message, recorded.Message, StringComparison.Ordinal);
        Assert.DoesNotContain(
            fixture.Store.ConnectionString, recorded.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AvailabilityReportsPostgreSqlSuccessAndFailureWithCorrelation()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        var service = new DatabaseAvailabilityService(
            fixture.Repository, fixture.Telemetry);
        await service.CheckAsync(CancellationToken.None);
        await fixture.Store.ExecuteAsync("DROP TABLE orders CASCADE;");
        await service.CheckAsync(CancellationToken.None);

        var samples = fixture.Channel.Items.OfType<AvailabilityTelemetry>().ToArray();
        Assert.Equal(2, samples.Length);
        Assert.True(samples[0].Success);
        Assert.False(samples[1].Success);
        Assert.Contains("PostgreSQL", samples[1].Message);
        Assert.All(samples, sample =>
        {
            Assert.Equal("orders-api-postgresql", sample.Name);
            Assert.Equal("orders-api", sample.Context.Cloud.RoleName);
            Assert.NotEmpty(sample.RunLocation);
            var dependency = Assert.Single(
                fixture.Channel.Items.OfType<DependencyTelemetry>(),
                dependency =>
                    dependency.Context.Operation.Id == sample.Context.Operation.Id);
            Assert.Equal(sample.Id, dependency.Context.Operation.ParentId);
            Assert.Equal(sample.Success, dependency.Success);
            Assert.Equal("PostgreSQL", dependency.Type);
        });
    }

    [Fact]
    public async Task CancellationIsRecordedWithoutDatabaseExceptionTelemetry()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            fixture.Repository.ProbeAsync(cancellation.Token));
        var dependency = Assert.Single(
            fixture.Channel.Items.OfType<DependencyTelemetry>());
        Assert.False(dependency.Success);
        Assert.Equal("Cancelled", dependency.ResultCode);
        Assert.Empty(fixture.Channel.Items.OfType<ExceptionTelemetry>());
    }

    [Fact]
    public async Task DatabaseStatusContainsOnlySafePostgreSqlFacts()
    {
        await using var fixture = await RepositoryFixture.CreateAsync(server);
        await fixture.Store.BootstrapAsync();

        var status = await fixture.Repository.GetDatabaseStatusAsync(
            CancellationToken.None);

        Assert.Equal("PostgreSQL", status.Provider);
        Assert.Equal("ready", status.Status);
        Assert.StartsWith("16.", status.ServerVersion, StringComparison.Ordinal);
        Assert.Equal(DatabaseBootstrap.CurrentMigrationVersion, status.SchemaVersion);
        Assert.True(status.DatabaseBytes > 0);
        Assert.DoesNotContain("Host=", status.ToString(), StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("Password=", status.ToString(), StringComparison.OrdinalIgnoreCase);
    }
}
