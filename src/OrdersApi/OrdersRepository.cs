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

    public async Task EnsureSchemaAsync(CancellationToken cancellationToken)
    {
        const string sql = """
            IF OBJECT_ID(N'dbo.Orders', N'U') IS NULL
            BEGIN
                CREATE TABLE dbo.Orders
                (
                    OrderId     BIGINT IDENTITY(1,1) PRIMARY KEY,
                    CustomerId  NVARCHAR(64)   NOT NULL,
                    ProductId   NVARCHAR(64)   NOT NULL,
                    Quantity    INT            NOT NULL,
                    UnitPrice   DECIMAL(18,2)  NOT NULL,
                    CreatedUtc  DATETIME2(3)   NOT NULL CONSTRAINT DF_Orders_CreatedUtc DEFAULT SYSUTCDATETIME()
                );
                CREATE INDEX IX_Orders_CreatedUtc ON dbo.Orders (CreatedUtc DESC);
            END;

            IF OBJECT_ID(N'dbo.StorageBallast', N'U') IS NULL
            BEGIN
                CREATE TABLE dbo.StorageBallast
                (
                    BallastId  BIGINT IDENTITY(1,1) PRIMARY KEY,
                    Payload    NVARCHAR(MAX)  NOT NULL,
                    CreatedUtc DATETIME2(3)   NOT NULL CONSTRAINT DF_Ballast_CreatedUtc DEFAULT SYSUTCDATETIME()
                );
            END;
            """;

        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = new SqlCommand(sql, connection) { CommandTimeout = 60 };
        await command.ExecuteNonQueryAsync(cancellationToken);
        logger.LogInformation("Orders schema verified.");
    }

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
        const string sql = """
            TRUNCATE TABLE dbo.StorageBallast;
            DBCC SHRINKDATABASE (0, 10) WITH NO_INFOMSGS;
            """;

        await using var connection = new SqlConnection(ConnectionString);
        await connection.OpenAsync(cancellationToken);
        await using var command = new SqlCommand(sql, connection) { CommandTimeout = 300 };
        return await command.ExecuteNonQueryAsync(cancellationToken);
    }
}
