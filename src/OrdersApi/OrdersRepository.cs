using System.Globalization;
using Microsoft.ApplicationInsights;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.Data.Sqlite;

namespace OrdersApi;

public sealed record OrderRequest(string CustomerId, string ProductId, int Quantity);

public sealed record OrderRecord(long OrderId, string CustomerId, string ProductId, int Quantity, decimal UnitPrice, DateTime CreatedUtc);

public sealed record StorageUsage(long UsedBytes, long MaxBytes, long AvailableBytes, long DatabaseBytes)
{
    public double UsedPercent => MaxBytes > 0 ? Math.Round(UsedBytes * 100.0 / MaxBytes, 2) : 0;
}

public sealed class OrdersRepository(
    OrdersDatabase database, TelemetryClient telemetry, ILogger<OrdersRepository> logger)
{
    public Task<long> CreateOrderAsync(OrderRequest request, decimal unitPrice, CancellationToken cancellationToken)
    {
        const string sql = """
            INSERT INTO Orders (CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc)
            VALUES (@CustomerId, @ProductId, @Quantity, @UnitPrice, @CreatedUtc)
            RETURNING OrderId;
            """;

        return ExecuteAsync("Orders.Insert", sql, async connection =>
        {
            await using var command = connection.CreateCommand();
            command.CommandText = sql;
            command.Parameters.AddWithValue("@CustomerId", request.CustomerId);
            command.Parameters.AddWithValue("@ProductId", request.ProductId);
            command.Parameters.AddWithValue("@Quantity", request.Quantity);
            command.Parameters.AddWithValue("@UnitPrice", unitPrice);
            command.Parameters.AddWithValue("@CreatedUtc", DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture));
            return Convert.ToInt64(await command.ExecuteScalarAsync(cancellationToken), CultureInfo.InvariantCulture);
        }, cancellationToken);
    }

    public Task<IReadOnlyList<OrderRecord>> GetRecentOrdersAsync(int take, CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT OrderId, CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc
            FROM Orders
            ORDER BY CreatedUtc DESC, OrderId DESC
            LIMIT @Take;
            """;

        return ExecuteAsync<IReadOnlyList<OrderRecord>>("Orders.List", sql, async connection =>
        {
            await using var command = connection.CreateCommand();
            command.CommandText = sql;
            command.Parameters.AddWithValue("@Take", take);
            var orders = new List<OrderRecord>();
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            while (await reader.ReadAsync(cancellationToken))
            {
                orders.Add(ReadOrder(reader));
            }
            return orders;
        }, cancellationToken);
    }

    public Task<OrderRecord?> GetOrderAsync(long orderId, CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT OrderId, CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc
            FROM Orders WHERE OrderId = @OrderId;
            """;

        return ExecuteAsync<OrderRecord?>("Orders.Get", sql, async connection =>
        {
            await using var command = connection.CreateCommand();
            command.CommandText = sql;
            command.Parameters.AddWithValue("@OrderId", orderId);
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            return await reader.ReadAsync(cancellationToken) ? ReadOrder(reader) : null;
        }, cancellationToken);
    }

    public Task<bool> UpdateQuantityAsync(long orderId, int quantity, CancellationToken cancellationToken)
    {
        const string sql = "UPDATE Orders SET Quantity = @Quantity WHERE OrderId = @OrderId;";

        return ExecuteAsync("Orders.UpdateQuantity", sql, async connection =>
        {
            await using var command = connection.CreateCommand();
            command.CommandText = sql;
            command.Parameters.AddWithValue("@Quantity", quantity);
            command.Parameters.AddWithValue("@OrderId", orderId);
            return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
        }, cancellationToken);
    }

    public Task ProbeAsync(CancellationToken cancellationToken)
    {
        const string sql = "SELECT OrderId, CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc FROM Orders LIMIT 1;";
        return ExecuteAsync("Orders.Readiness", sql, async connection =>
        {
            await using var command = connection.CreateCommand();
            command.CommandText = sql;
            await command.ExecuteScalarAsync(cancellationToken);
            return true;
        }, cancellationToken);
    }

    public async Task<StorageUsage> GetStorageUsageAsync(CancellationToken cancellationToken)
    {
        const string sql = "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size();";
        var databaseBytes = await ExecuteAsync("Orders.Storage", sql, async connection =>
        {
            await using var command = connection.CreateCommand();
            command.CommandText = sql;
            return Convert.ToInt64(await command.ExecuteScalarAsync(cancellationToken), CultureInfo.InvariantCulture);
        }, cancellationToken);

        // Stat the database directory, not the OS drive: /var/lib/orders is a separate VM data disk.
        var disk = new DriveInfo(Path.GetDirectoryName(database.DataSource)!);
        var maxBytes = disk.TotalSize;
        var availableBytes = disk.AvailableFreeSpace;
        return new StorageUsage(maxBytes - availableBytes, maxBytes, availableBytes, databaseBytes);
    }

    private async Task<T> ExecuteAsync<T>(
        string name, string sql, Func<SqliteConnection, Task<T>> execute, CancellationToken cancellationToken)
    {
        using var operation = telemetry.StartOperation<DependencyTelemetry>(name);
        var dependency = operation.Telemetry;
        dependency.Type = "SQLite";
        dependency.Target = database.DataSource;
        dependency.Data = sql;
        dependency.Success = false;
        dependency.ResultCode = "Failed";

        try
        {
            await using var connection = database.CreateConnection();
            await connection.OpenAsync(cancellationToken);
            await OrdersDatabase.ConfigureConnectionAsync(connection, cancellationToken);
            var result = await execute(connection);
            dependency.Success = true;
            dependency.ResultCode = "0";
            return result;
        }
        catch (SqliteException ex)
        {
            dependency.ResultCode = ex.SqliteErrorCode.ToString(CultureInfo.InvariantCulture);
            telemetry.TrackException(ex, new Dictionary<string, string>
            {
                ["database.operation"] = name,
                ["sqlite.error_code"] = dependency.ResultCode,
                ["sqlite.extended_error_code"] = ex.SqliteExtendedErrorCode.ToString(CultureInfo.InvariantCulture)
            });
            logger.LogError(
                "SQLite operation {Operation} failed ({ErrorCode}/{ExtendedErrorCode}): {Message}",
                name, ex.SqliteErrorCode, ex.SqliteExtendedErrorCode, ex.Message);
            throw;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            dependency.ResultCode = "Cancelled";
            throw;
        }
    }

    private static OrderRecord ReadOrder(SqliteDataReader reader) => new(
        reader.GetInt64(0),
        reader.GetString(1),
        reader.GetString(2),
        reader.GetInt32(3),
        reader.GetDecimal(4),
        DateTime.Parse(reader.GetString(5), CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind));
}
