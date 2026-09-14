using System.Data;
using Microsoft.Data.SqlClient;

namespace OrdersApi;

public sealed record OrderRequest(string CustomerId, string ProductId, int Quantity);

public sealed record OrderRecord(long OrderId, string CustomerId, string ProductId, int Quantity, decimal UnitPrice, DateTime CreatedUtc);

/// <summary>Data access for the orders schema. Every statement is parameterized.</summary>
public sealed class OrdersRepository(IConfiguration configuration, ILogger<OrdersRepository> logger)
{
    // Resolved on first use so the service still starts, and still emits telemetry, when the
    // database is misconfigured. A silent crash loop teaches nobody anything.
    private string ConnectionString =>
        configuration.GetConnectionString("OrdersDb")
        ?? throw new InvalidOperationException("Connection string 'OrdersDb' is not configured.");

    public async Task<long> CreateOrderAsync(OrderRequest request, decimal unitPrice, CancellationToken cancellationToken)
    {
        const string sql = """
            INSERT INTO dbo.Orders (CustomerId, ProductId, Quantity, UnitPrice)
            OUTPUT INSERTED.OrderId
            VALUES (@CustomerId, @ProductId, @Quantity, @UnitPrice);
            """;

        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = new SqlCommand(sql, connection);
        command.Parameters.Add("@CustomerId", SqlDbType.NVarChar, 64).Value = request.CustomerId;
        command.Parameters.Add("@ProductId", SqlDbType.NVarChar, 64).Value = request.ProductId;
        command.Parameters.Add("@Quantity", SqlDbType.Int).Value = request.Quantity;
        command.Parameters.Add("@UnitPrice", SqlDbType.Decimal).Value = unitPrice;

        var result = await command.ExecuteScalarAsync(cancellationToken);
        return Convert.ToInt64(result);
    }

    public async Task<IReadOnlyList<OrderRecord>> GetRecentOrdersAsync(int take, CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT TOP (@Take) OrderId, CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc
            FROM dbo.Orders
            ORDER BY CreatedUtc DESC;
            """;

        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = new SqlCommand(sql, connection);
        command.Parameters.Add("@Take", SqlDbType.Int).Value = take;

        var orders = new List<OrderRecord>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            orders.Add(new OrderRecord(
                reader.GetInt64(0),
                reader.GetString(1),
                reader.GetString(2),
                reader.GetInt32(3),
                reader.GetDecimal(4),
                reader.GetDateTime(5)));
        }

        return orders;
    }

    public async Task<bool> UpdateQuantityAsync(long orderId, int quantity, CancellationToken cancellationToken)
    {
        const string sql = """
            UPDATE dbo.Orders SET Quantity = @Quantity WHERE OrderId = @OrderId;
            """;

        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = new SqlCommand(sql, connection);
        command.Parameters.Add("@Quantity", SqlDbType.Int).Value = quantity;
        command.Parameters.Add("@OrderId", SqlDbType.BigInt).Value = orderId;
        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    public async Task<(long UsedBytes, long MaxBytes)> GetStorageUsageAsync(CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT
                CAST(SUM(CAST(FILEPROPERTY(name, 'SpaceUsed') AS BIGINT)) * 8192 AS BIGINT) AS UsedBytes,
                CAST(DATABASEPROPERTYEX(DB_NAME(), 'MaxSizeInBytes') AS BIGINT) AS MaxBytes
            FROM sys.database_files
            WHERE type_desc = 'ROWS';
            """;

        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = new SqlCommand(sql, connection);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);

        if (!await reader.ReadAsync(cancellationToken))
        {
            return (0, 0);
        }

        var used = reader.IsDBNull(0) ? 0L : reader.GetInt64(0);
        var max = reader.IsDBNull(1) ? 0L : reader.GetInt64(1);
        return (used, max);
    }

    /// <summary>
    /// Writes padded rows in batches until the database reaches <paramref name="targetPercent"/> of its
    /// maximum size. Used only by the Module 10 storage exhaustion lab.
    /// </summary>
    public async Task<long> FillStorageAsync(
        int targetPercent,
        Action<long> onProgress,
        CancellationToken cancellationToken)
    {
        const int rowsPerBatch = 64;
        const int payloadCharacters = 60000;

        const string insertSql = """
            INSERT INTO dbo.StorageBallast (Payload)
            SELECT REPLICATE(CAST(N'X' AS NVARCHAR(MAX)), @PayloadLength)
            FROM (SELECT TOP (@RowCount) 1 AS n FROM sys.all_columns) AS s;
            """;

        long bytesWritten = 0;

        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);

        while (!cancellationToken.IsCancellationRequested)
        {
            var (used, max) = await GetStorageUsageAsync(cancellationToken);
            if (max > 0 && used * 100 / max >= targetPercent)
            {
                logger.LogWarning(
                    "Storage fill reached target. Used {UsedBytes} of {MaxBytes} bytes.", used, max);
                break;
            }

            await using var command = new SqlCommand(insertSql, connection) { CommandTimeout = 120 };
            command.Parameters.Add("@PayloadLength", SqlDbType.Int).Value = payloadCharacters;
            command.Parameters.Add("@RowCount", SqlDbType.Int).Value = rowsPerBatch;
            await command.ExecuteNonQueryAsync(cancellationToken);

            bytesWritten += (long)rowsPerBatch * payloadCharacters * 2;
            onProgress(bytesWritten);
        }

        return bytesWritten;
    }

    public async Task<int> ReleaseStorageAsync(CancellationToken cancellationToken)
    {
        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = new SqlCommand("dbo.ReleaseStorageBallast", connection)
        {
            CommandType = CommandType.StoredProcedure,
            CommandTimeout = 300
        };
        return await command.ExecuteNonQueryAsync(cancellationToken);
    }
}
