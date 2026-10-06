using System.Globalization;
using Microsoft.ApplicationInsights;
using Microsoft.ApplicationInsights.DataContracts;
using Npgsql;
using NpgsqlTypes;

namespace OrdersApi;

public sealed record OrderRequest(string CustomerId, string ProductId, int Quantity);

public sealed record OrderRecord(
    long OrderId,
    string CustomerId,
    string ProductId,
    int Quantity,
    decimal UnitPrice,
    DateTime CreatedUtc);

public sealed record DatabaseStatus(
    string Provider,
    string Status,
    string ServerVersion,
    long SchemaVersion,
    long DatabaseBytes);

internal sealed record IdempotentOrderCreation(long OrderId, decimal UnitPrice, bool Replayed);

public sealed class OrdersRepository(
    OrdersDatabase database, TelemetryClient telemetry, ILogger<OrdersRepository> logger)
{
    private const string InsertOrderSql = """
        INSERT INTO orders (customer_id, product_id, quantity, unit_price, created_utc)
        VALUES (@customer_id, @product_id, @quantity, @unit_price, @created_utc)
        RETURNING order_id;
        """;

    public Task<long> CreateOrderAsync(
        OrderRequest request, decimal unitPrice, CancellationToken cancellationToken) =>
        ExecuteAsync(
            "Orders.Insert",
            InsertOrderSql,
            connection => InsertOrderAsync(
                connection, null, request, unitPrice, cancellationToken),
            cancellationToken);

    internal Task<IdempotentOrderCreation?> CreateOrderIdempotentlyAsync(
        OrderRequest request,
        decimal unitPrice,
        string requestId,
        CancellationToken cancellationToken)
    {
        const string telemetrySql = """
            INSERT INTO order_requests (...) ON CONFLICT (request_id) DO NOTHING;
            INSERT INTO orders (...) RETURNING order_id;
            UPDATE order_requests SET order_id = ...;
            """;

        return ExecuteAsync<IdempotentOrderCreation?>(
            "Orders.Insert",
            telemetrySql,
            async connection =>
            {
                await using var transaction =
                    await connection.BeginTransactionAsync(cancellationToken);
                await using var reservation = connection.CreateCommand();
                reservation.Transaction = transaction;
                reservation.CommandText = """
                    INSERT INTO order_requests
                        (request_id, customer_id, product_id, quantity, unit_price)
                    VALUES
                        (@request_id, @customer_id, @product_id, @quantity, @unit_price)
                    ON CONFLICT (request_id) DO NOTHING;
                    """;
                reservation.Parameters.AddWithValue("request_id", requestId);
                reservation.Parameters.AddWithValue("customer_id", request.CustomerId);
                reservation.Parameters.AddWithValue("product_id", request.ProductId);
                reservation.Parameters.AddWithValue("quantity", request.Quantity);
                reservation.Parameters.AddWithValue("unit_price", unitPrice);

                if (await reservation.ExecuteNonQueryAsync(cancellationToken) == 0)
                {
                    await using var existing = connection.CreateCommand();
                    existing.Transaction = transaction;
                    existing.CommandText = """
                        SELECT customer_id, product_id, quantity, unit_price, order_id
                        FROM order_requests
                        WHERE request_id = @request_id;
                        """;
                    existing.Parameters.AddWithValue("request_id", requestId);
                    await using var reader = await existing.ExecuteReaderAsync(cancellationToken);
                    if (!await reader.ReadAsync(cancellationToken) || reader.IsDBNull(4))
                    {
                        throw new OrdersDatabaseUnavailableException(
                            "The idempotency record is incomplete.");
                    }

                    var sameRequest =
                        string.Equals(reader.GetString(0), request.CustomerId, StringComparison.Ordinal)
                        && string.Equals(reader.GetString(1), request.ProductId, StringComparison.Ordinal)
                        && reader.GetInt32(2) == request.Quantity;
                    var savedUnitPrice = reader.GetDecimal(3);
                    var orderId = reader.GetInt64(4);
                    await reader.DisposeAsync();
                    await transaction.CommitAsync(cancellationToken);
                    return sameRequest
                        ? new IdempotentOrderCreation(orderId, savedUnitPrice, Replayed: true)
                        : null;
                }

                var createdOrderId = await InsertOrderAsync(
                    connection, transaction, request, unitPrice, cancellationToken);
                await using var complete = connection.CreateCommand();
                complete.Transaction = transaction;
                complete.CommandText = """
                    UPDATE order_requests
                    SET order_id = @order_id
                    WHERE request_id = @request_id;
                    """;
                complete.Parameters.AddWithValue("order_id", createdOrderId);
                complete.Parameters.AddWithValue("request_id", requestId);
                if (await complete.ExecuteNonQueryAsync(cancellationToken) != 1)
                {
                    throw new OrdersDatabaseUnavailableException(
                        "The idempotency record could not be completed.");
                }

                await transaction.CommitAsync(cancellationToken);
                return new IdempotentOrderCreation(
                    createdOrderId, unitPrice, Replayed: false);
            },
            cancellationToken);
    }

    public Task<IReadOnlyList<OrderRecord>> GetRecentOrdersAsync(
        int take, CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT order_id, customer_id, product_id, quantity, unit_price, created_utc
            FROM orders
            ORDER BY created_utc DESC, order_id DESC
            LIMIT @take;
            """;

        return ExecuteAsync<IReadOnlyList<OrderRecord>>(
            "Orders.List",
            sql,
            async connection =>
            {
                await using var command = connection.CreateCommand();
                command.CommandText = sql;
                command.Parameters.AddWithValue("take", take);
                var orders = new List<OrderRecord>();
                await using var reader = await command.ExecuteReaderAsync(cancellationToken);
                while (await reader.ReadAsync(cancellationToken))
                {
                    orders.Add(ReadOrder(reader));
                }
                return orders;
            },
            cancellationToken);
    }

    public Task<OrderRecord?> GetOrderAsync(
        long orderId, CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT order_id, customer_id, product_id, quantity, unit_price, created_utc
            FROM orders
            WHERE order_id = @order_id;
            """;

        return ExecuteAsync<OrderRecord?>(
            "Orders.Get",
            sql,
            async connection =>
            {
                await using var command = connection.CreateCommand();
                command.CommandText = sql;
                command.Parameters.AddWithValue("order_id", orderId);
                await using var reader = await command.ExecuteReaderAsync(cancellationToken);
                return await reader.ReadAsync(cancellationToken) ? ReadOrder(reader) : null;
            },
            cancellationToken);
    }

    public Task<bool> UpdateQuantityAsync(
        long orderId, int quantity, CancellationToken cancellationToken)
    {
        const string sql =
            "UPDATE orders SET quantity = @quantity WHERE order_id = @order_id;";

        return ExecuteAsync(
            "Orders.UpdateQuantity",
            sql,
            async connection =>
            {
                await using var command = connection.CreateCommand();
                command.CommandText = sql;
                command.Parameters.AddWithValue("quantity", quantity);
                command.Parameters.AddWithValue("order_id", orderId);
                return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
            },
            cancellationToken);
    }

    public Task ProbeAsync(CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT
                COALESCE((SELECT MAX(version) FROM orders_schema_migrations), 0),
                to_regclass('orders') IS NOT NULL,
                to_regclass('order_requests') IS NOT NULL;
            """;
        return ExecuteAsync(
            "Orders.Readiness",
            sql,
            async connection =>
            {
                await using var command = connection.CreateCommand();
                command.CommandText = sql;
                await using var reader = await command.ExecuteReaderAsync(cancellationToken);
                if (!await reader.ReadAsync(cancellationToken)
                    || reader.GetInt64(0) != DatabaseBootstrap.CurrentMigrationVersion
                    || !reader.GetBoolean(1)
                    || !reader.GetBoolean(2))
                {
                    throw new OrdersDatabaseUnavailableException(
                        "The required PostgreSQL schema is unavailable.");
                }
                return true;
            },
            cancellationToken);
    }

    public Task<DatabaseStatus> GetDatabaseStatusAsync(
        CancellationToken cancellationToken)
    {
        const string sql = """
            SELECT
                pg_database_size(current_database()),
                current_setting('server_version'),
                COALESCE((SELECT MAX(version) FROM orders_schema_migrations), 0);
            """;
        return ExecuteAsync(
            "Orders.DatabaseStatus",
            sql,
            async connection =>
            {
                await using var command = connection.CreateCommand();
                command.CommandText = sql;
                await using var reader = await command.ExecuteReaderAsync(cancellationToken);
                if (!await reader.ReadAsync(cancellationToken))
                {
                    throw new OrdersDatabaseUnavailableException(
                        "PostgreSQL did not return database status.");
                }
                return new DatabaseStatus(
                    Provider: "PostgreSQL",
                    Status: "ready",
                    ServerVersion: reader.GetString(1),
                    SchemaVersion: reader.GetInt64(2),
                    DatabaseBytes: reader.GetInt64(0));
            },
            cancellationToken);
    }

    private async Task<T> ExecuteAsync<T>(
        string name,
        string sql,
        Func<NpgsqlConnection, Task<T>> execute,
        CancellationToken cancellationToken)
    {
        using var operation = telemetry.StartOperation<DependencyTelemetry>(name);
        var dependency = operation.Telemetry;
        dependency.Type = "PostgreSQL";
        dependency.Target = database.TelemetryTarget;
        dependency.Data = sql;
        dependency.Success = false;
        dependency.ResultCode = "Failed";

        try
        {
            await using var connection = await database.OpenConnectionAsync(cancellationToken);
            var result = await execute(connection);
            dependency.Success = true;
            dependency.ResultCode = "0";
            return result;
        }
        catch (PostgresException ex)
        {
            RecordFailure(name, ex.SqlState, ex.IsTransient);
            throw;
        }
        catch (NpgsqlException ex)
        {
            RecordFailure(name, "Connectivity", ex.IsTransient);
            throw;
        }
        catch (TimeoutException)
        {
            RecordFailure(name, "Timeout", transient: true);
            throw;
        }
        catch (OrdersDatabaseUnavailableException)
        {
            RecordFailure(name, "SchemaUnavailable", transient: false);
            throw;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            dependency.ResultCode = "Cancelled";
            throw;
        }

        void RecordFailure(string operationName, string resultCode, bool transient)
        {
            dependency.ResultCode = resultCode;
            var properties = new Dictionary<string, string>
            {
                ["database.operation"] = operationName,
                ["postgresql.result_code"] = resultCode,
                ["postgresql.transient"] = transient.ToString(CultureInfo.InvariantCulture)
            };
            telemetry.TrackException(
                new DatabaseOperationException(operationName, resultCode, transient),
                properties);
            logger.LogError(
                "PostgreSQL operation {Operation} failed ({ResultCode}; transient={Transient}).",
                operationName,
                resultCode,
                transient);
        }
    }

    private static async Task<long> InsertOrderAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction? transaction,
        OrderRequest request,
        decimal unitPrice,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = InsertOrderSql;
        command.Parameters.AddWithValue("customer_id", request.CustomerId);
        command.Parameters.AddWithValue("product_id", request.ProductId);
        command.Parameters.AddWithValue("quantity", request.Quantity);
        command.Parameters.AddWithValue("unit_price", unitPrice);
        command.Parameters.AddWithValue(
            "created_utc", NpgsqlDbType.TimestampTz, DateTime.UtcNow);
        return Convert.ToInt64(
            await command.ExecuteScalarAsync(cancellationToken),
            CultureInfo.InvariantCulture);
    }

    private static OrderRecord ReadOrder(NpgsqlDataReader reader) => new(
        reader.GetInt64(0),
        reader.GetString(1),
        reader.GetString(2),
        reader.GetInt32(3),
        reader.GetDecimal(4),
        reader.GetDateTime(5));
}
