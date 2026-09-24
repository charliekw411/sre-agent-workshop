using System.Globalization;
using Microsoft.Data.Sqlite;

namespace OrdersApi;

internal static class DatabaseBootstrap
{
    public static async Task<int> RunAsync(string[] args)
    {
        try
        {
            var configuration = new ConfigurationBuilder()
                .AddJsonFile("appsettings.json", optional: true)
                .AddEnvironmentVariables()
                .AddCommandLine(args)
                .Build();

            var database = new OrdersDatabase(configuration.GetConnectionString("OrdersDb"));
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            await InitializeAsync(database, timeout.Token);
            Console.WriteLine("Orders SQLite database bootstrap completed.");
            return 0;
        }
        catch (InvalidOperationException ex)
        {
            Console.Error.WriteLine($"Orders database bootstrap failed: {ex.Message}");
        }
        catch (SqliteException ex)
        {
            Console.Error.WriteLine(
                $"Orders database bootstrap failed (SQLite error {ex.SqliteErrorCode}/{ex.SqliteExtendedErrorCode}): {ex.Message}");
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("Orders database bootstrap timed out.");
        }

        return 1;
    }

    internal static async Task InitializeAsync(OrdersDatabase database, CancellationToken cancellationToken)
    {
        await using var connection = database.CreateConnection(allowCreate: true);
        await connection.OpenAsync(cancellationToken);
        await OrdersDatabase.ConfigureConnectionAsync(connection, cancellationToken);

        await using (var journal = connection.CreateCommand())
        {
            journal.CommandText = "PRAGMA journal_mode = WAL;";
            var mode = Convert.ToString(await journal.ExecuteScalarAsync(cancellationToken), CultureInfo.InvariantCulture);
            if (!string.Equals(mode, "wal", StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("The orders database must support SQLite WAL mode.");
            }
        }

        await using var transaction = connection.BeginTransaction();
        await using (var schema = connection.CreateCommand())
        {
            schema.Transaction = transaction;
            schema.CommandText = """
                CREATE TABLE IF NOT EXISTS Orders
                (
                    OrderId    INTEGER PRIMARY KEY AUTOINCREMENT,
                    CustomerId TEXT NOT NULL CHECK (length(CustomerId) BETWEEN 1 AND 64 AND length(trim(CustomerId)) > 0),
                    ProductId  TEXT NOT NULL CHECK (length(ProductId) BETWEEN 1 AND 64 AND length(trim(ProductId)) > 0),
                    Quantity   INTEGER NOT NULL CHECK (Quantity BETWEEN 1 AND 1000),
                    UnitPrice  TEXT NOT NULL,
                    CreatedUtc TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS IX_Orders_CreatedUtc ON Orders (CreatedUtc DESC, OrderId DESC);
                """;
            await schema.ExecuteNonQueryAsync(cancellationToken);
        }

        for (var index = 0; index < SampleProducts.All.Count; index++)
        {
            var product = SampleProducts.All[index];
            await using var seed = connection.CreateCommand();
            seed.Transaction = transaction;
            // Ignore only an existing seed ID; do not mask other constraint or storage errors.
            seed.CommandText = """
                INSERT INTO Orders (OrderId, CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc)
                VALUES (@OrderId, @CustomerId, @ProductId, @Quantity, @UnitPrice, @CreatedUtc)
                ON CONFLICT(OrderId) DO NOTHING;
                """;
            seed.Parameters.AddWithValue("@OrderId", -(index + 1));
            seed.Parameters.AddWithValue("@CustomerId", "workshop-seed");
            seed.Parameters.AddWithValue("@ProductId", product.ProductId);
            seed.Parameters.AddWithValue("@Quantity", 1);
            seed.Parameters.AddWithValue("@UnitPrice", product.Price);
            seed.Parameters.AddWithValue("@CreatedUtc", "2026-01-01T00:00:00.0000000Z");
            await seed.ExecuteNonQueryAsync(cancellationToken);
        }

        await transaction.CommitAsync(cancellationToken);
    }
}
