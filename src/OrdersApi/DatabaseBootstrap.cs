using System.Data;
using Microsoft.Data.SqlClient;

namespace OrdersApi;

internal static class DatabaseBootstrap
{
    private const int MaxAttempts = 8;

    public static async Task<int> RunAsync(string[] args)
    {
        try
        {
            var configuration = new ConfigurationBuilder()
                .AddJsonFile("appsettings.json", optional: true)
                .AddEnvironmentVariables()
                .AddCommandLine(args)
                .Build();

            if (!Guid.TryParse(configuration["ORDERS_IDENTITY_PRINCIPAL_ID"], out var principalId)
                || principalId == Guid.Empty)
            {
                Console.Error.WriteLine("Bootstrap requires ORDERS_IDENTITY_PRINCIPAL_ID to be a nonempty object GUID.");
                return 1;
            }

            var configuredConnection = configuration.GetConnectionString("OrdersDb");
            if (string.IsNullOrWhiteSpace(configuredConnection))
            {
                Console.Error.WriteLine("Bootstrap requires ConnectionStrings__OrdersDb.");
                return 1;
            }

            var connectionString = new SqlConnectionStringBuilder(configuredConnection)
            {
                ConnectTimeout = 15,
                ConnectRetryCount = 0,
                // A cancelled batch cannot leave IDENTITY_INSERT enabled on a pooled session.
                Pooling = false
            }.ConnectionString;

            using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(10));
            for (var attempt = 1; attempt <= MaxAttempts; attempt++)
            {
                try
                {
                    await InitializeAsync(connectionString, principalId, timeout.Token);
                    Console.WriteLine("Orders database bootstrap completed.");
                    return 0;
                }
                catch (Exception ex) when (attempt < MaxAttempts && !timeout.IsCancellationRequested)
                {
                    // Exception messages can contain connection details or identity provider responses.
                    Console.Error.WriteLine(ex is SqlException sql
                        ? $"Bootstrap attempt {attempt}/{MaxAttempts} failed (SQL error {sql.Number}); retrying."
                        : $"Bootstrap attempt {attempt}/{MaxAttempts} failed; retrying.");
                    await Task.Delay(TimeSpan.FromSeconds(Math.Min(5 * attempt, 30)), timeout.Token);
                }
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex is SqlException sql
                ? $"Orders database bootstrap failed (SQL error {sql.Number})."
                : "Orders database bootstrap failed. Check configuration, identity access, and database availability.");
        }

        return 1;
    }

    private static async Task InitializeAsync(
        string connectionString, Guid principalId, CancellationToken cancellationToken)
    {
        await using var connection = new SqlConnection(connectionString);
        await connection.OpenAsync(cancellationToken);
        await using var transaction = (SqlTransaction)await connection.BeginTransactionAsync(cancellationToken);

        await ExecuteAsync("""
            SET XACT_ABORT ON;
            DECLARE @LockResult INT;
            EXEC @LockResult = sys.sp_getapplock
                @Resource = N'OrdersApi.DatabaseBootstrap',
                @LockMode = N'Exclusive',
                @LockOwner = N'Transaction',
                @LockTimeout = 60000;
            IF @LockResult < 0
                THROW 51000, 'Could not acquire the orders bootstrap lock.', 1;

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
            """, connection, transaction, cancellationToken);

        await ExecuteAsync("""
            CREATE OR ALTER PROCEDURE dbo.ReleaseStorageBallast
            WITH EXECUTE AS OWNER
            AS
            BEGIN
                SET NOCOUNT ON;
                TRUNCATE TABLE dbo.StorageBallast;
                DBCC SHRINKDATABASE (0, 10) WITH NO_INFOMSGS;
            END;
            """, connection, transaction, cancellationToken);

        // CREATE USER requires a SID literal. Only hex derived from a validated GUID is interpolated.
        // Guid.ToByteArray uses the byte order expected by SQL Server for an Entra object ID.
        var sid = principalId.ToByteArray();
        var userSql = $"""
            IF DATABASE_PRINCIPAL_ID(N'orders-api') IS NULL
                CREATE USER [orders-api] WITH SID = 0x{Convert.ToHexString(sid)}, TYPE = E;
            ELSE IF NOT EXISTS (
                SELECT 1 FROM sys.database_principals
                WHERE name = N'orders-api' AND sid = @OrdersSid AND type = 'E')
                THROW 51001, 'The orders-api database user does not match the runtime identity.', 1;

            GRANT SELECT, INSERT, UPDATE ON OBJECT::dbo.Orders TO [orders-api];
            GRANT SELECT, INSERT ON OBJECT::dbo.StorageBallast TO [orders-api];
            GRANT EXECUTE ON OBJECT::dbo.ReleaseStorageBallast TO [orders-api];
            """;
        await using (var command = new SqlCommand(userSql, connection, transaction) { CommandTimeout = 120 })
        {
            command.Parameters.Add("@OrdersSid", SqlDbType.VarBinary, 16).Value = sid;
            await command.ExecuteNonQueryAsync(cancellationToken);
        }

        await ExecuteAsync("""
            BEGIN TRY
                SET IDENTITY_INSERT dbo.Orders ON;
                INSERT INTO dbo.Orders WITH (TABLOCKX)
                    (OrderId, CustomerId, ProductId, Quantity, UnitPrice, CreatedUtc)
                SELECT seed.OrderId, N'workshop-seed', seed.ProductId, 1, seed.UnitPrice,
                    CONVERT(DATETIME2(3), '2026-01-01T00:00:00', 126)
                FROM (VALUES
                    (CONVERT(BIGINT, -1), N'SKU-1001', CONVERT(DECIMAL(18,2), 129.99)),
                    (CONVERT(BIGINT, -2), N'SKU-1002', CONVERT(DECIMAL(18,2), 349.00)),
                    (CONVERT(BIGINT, -3), N'SKU-1003', CONVERT(DECIMAL(18,2), 219.50)),
                    (CONVERT(BIGINT, -4), N'SKU-1004', CONVERT(DECIMAL(18,2), 45.75)),
                    (CONVERT(BIGINT, -5), N'SKU-1005', CONVERT(DECIMAL(18,2), 189.00))
                ) AS seed(OrderId, ProductId, UnitPrice)
                WHERE NOT EXISTS (SELECT 1 FROM dbo.Orders existing WHERE existing.OrderId = seed.OrderId);
                SET IDENTITY_INSERT dbo.Orders OFF;

                -- An initial explicit negative identity must not leave automatic IDs in the seed range.
                -- Never rewind the identity of a database that has already issued positive IDs.
                IF IDENT_CURRENT(N'dbo.Orders') < 0
                    AND NOT EXISTS (SELECT 1 FROM dbo.Orders WHERE OrderId > 0)
                    DBCC CHECKIDENT (N'dbo.Orders', RESEED, 0) WITH NO_INFOMSGS;
            END TRY
            BEGIN CATCH
                SET IDENTITY_INSERT dbo.Orders OFF;
                THROW;
            END CATCH;
            """, connection, transaction, cancellationToken);

        await transaction.CommitAsync(cancellationToken);
    }

    private static async Task ExecuteAsync(
        string sql, SqlConnection connection, SqlTransaction transaction, CancellationToken cancellationToken)
    {
        await using var command = new SqlCommand(sql, connection, transaction) { CommandTimeout = 120 };
        await command.ExecuteNonQueryAsync(cancellationToken);
    }
}
