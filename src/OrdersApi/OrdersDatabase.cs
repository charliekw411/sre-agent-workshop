using Microsoft.Data.Sqlite;

namespace OrdersApi;

public sealed class OrdersDatabase
{
    public const int BusyTimeoutSeconds = 5;
    private readonly string _connectionString;

    public OrdersDatabase(string? connectionString)
    {
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            throw new InvalidOperationException(
                "ConnectionStrings__OrdersDb is required, for example: Data Source=/var/lib/orders/orders.db.");
        }

        SqliteConnectionStringBuilder builder;
        try
        {
            builder = new SqliteConnectionStringBuilder(connectionString);
        }
        catch (ArgumentException ex)
        {
            throw new InvalidOperationException(
                "ConnectionStrings__OrdersDb must be a valid SQLite connection string.", ex);
        }

        if (string.IsNullOrWhiteSpace(builder.DataSource)
            || !Path.IsPathFullyQualified(builder.DataSource)
            || builder.DataSource.IndexOfAny(Path.GetInvalidPathChars()) >= 0
            || string.IsNullOrEmpty(Path.GetFileName(builder.DataSource))
            || builder.Mode is SqliteOpenMode.Memory or SqliteOpenMode.ReadOnly
            || !string.IsNullOrEmpty(builder.Password))
        {
            throw new InvalidOperationException(
                "ConnectionStrings__OrdersDb must name an absolute, writable SQLite file path, without a password.");
        }

        DataSource = Path.GetFullPath(builder.DataSource);
        builder.DataSource = DataSource;
        builder.Mode = SqliteOpenMode.ReadWrite;
        builder.Cache = SqliteCacheMode.Private;
        builder.Pooling = false;
        builder.DefaultTimeout = BusyTimeoutSeconds;
        _connectionString = builder.ConnectionString;
    }

    public string DataSource { get; }

    public SqliteConnection CreateConnection(bool allowCreate = false)
    {
        var builder = new SqliteConnectionStringBuilder(_connectionString)
        {
            // Only the explicit bootstrap command may create a database.
            Mode = allowCreate ? SqliteOpenMode.ReadWriteCreate : SqliteOpenMode.ReadWrite
        };
        return new SqliteConnection(builder.ConnectionString);
    }

    public static async Task ConfigureConnectionAsync(
        SqliteConnection connection, CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = "PRAGMA busy_timeout = 5000;";
        await command.ExecuteNonQueryAsync(cancellationToken);
    }
}
