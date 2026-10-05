using Azure.Core;
using Azure.Identity;
using Npgsql;

namespace OrdersApi;

public sealed class OrdersDatabase : IAsyncDisposable
{
    internal const int CommandTimeoutSeconds = 5;
    internal const string ManagedIdentityAuthentication = "ManagedIdentity";
    internal const string PasswordAuthentication = "Password";
    private static readonly string[] TokenScopes =
        ["https://ossrdbms-aad.database.windows.net/.default"];
    private readonly NpgsqlDataSource _dataSource;

    public OrdersDatabase(
        string? connectionString,
        string? authentication = PasswordAuthentication,
        string? managedIdentityClientId = null)
    {
        if (string.IsNullOrWhiteSpace(connectionString))
        {
            throw new InvalidOperationException(
                "ConnectionStrings__OrdersDb is required and must identify a PostgreSQL database.");
        }

        authentication ??= PasswordAuthentication;
        NpgsqlConnectionStringBuilder connection;
        try
        {
            connection = new NpgsqlConnectionStringBuilder(connectionString);
        }
        catch (ArgumentException ex)
        {
            throw new InvalidOperationException(
                "ConnectionStrings__OrdersDb must be a valid PostgreSQL connection string.", ex);
        }

        if (string.IsNullOrWhiteSpace(connection.Host)
            || string.IsNullOrWhiteSpace(connection.Database)
            || string.IsNullOrWhiteSpace(connection.Username))
        {
            throw new InvalidOperationException(
                "ConnectionStrings__OrdersDb must include PostgreSQL Host, Database, and Username values.");
        }

        if (!string.Equals(authentication, PasswordAuthentication, StringComparison.Ordinal)
            && !string.Equals(authentication, ManagedIdentityAuthentication, StringComparison.Ordinal))
        {
            throw new InvalidOperationException(
                "OrdersDatabase__Authentication must be Password or ManagedIdentity.");
        }

        connection.Pooling = true;
        connection.Timeout = CommandTimeoutSeconds;
        connection.CommandTimeout = CommandTimeoutSeconds;
        connection.CancellationTimeout = 2_000;
        connection.IncludeErrorDetail = false;
        connection.ApplicationName = "orders-api";
        connection.MaxPoolSize = 20;
        connection.MinPoolSize = 0;

        var dataSourceBuilder = new NpgsqlDataSourceBuilder(connection.ConnectionString);
        if (string.Equals(authentication, ManagedIdentityAuthentication, StringComparison.Ordinal))
        {
            if (!string.IsNullOrEmpty(connection.Password))
            {
                throw new InvalidOperationException(
                    "ManagedIdentity database authentication must not include a password.");
            }
            if (connection.SslMode != SslMode.VerifyFull)
            {
                throw new InvalidOperationException(
                    "ManagedIdentity database authentication requires SSL Mode=VerifyFull.");
            }
            if (!string.IsNullOrWhiteSpace(managedIdentityClientId)
                && !Guid.TryParse(managedIdentityClientId, out _))
            {
                throw new InvalidOperationException(
                    "OrdersDatabase__ManagedIdentityClientId must be a client ID GUID.");
            }

            var identity = string.IsNullOrWhiteSpace(managedIdentityClientId)
                ? ManagedIdentityId.SystemAssigned
                : ManagedIdentityId.FromUserAssignedClientId(managedIdentityClientId);
            TokenCredential credential = new ManagedIdentityCredential(identity);
            dataSourceBuilder.UsePeriodicPasswordProvider(
                async (_, cancellationToken) =>
                {
                    var token = await credential.GetTokenAsync(
                        new TokenRequestContext(TokenScopes), cancellationToken);
                    return token.Token;
                },
                TimeSpan.FromMinutes(50),
                TimeSpan.FromSeconds(5));
        }
        else if (string.IsNullOrEmpty(connection.Password))
        {
            throw new InvalidOperationException(
                "Password database authentication requires a password from an external environment value.");
        }

        DatabaseName = connection.Database;
        TelemetryTarget = $"PostgreSQL/{connection.Database}";
        _dataSource = dataSourceBuilder.Build();
    }

    public string DatabaseName { get; }
    public string TelemetryTarget { get; }

    public NpgsqlConnection CreateConnection() => _dataSource.CreateConnection();

    public ValueTask<NpgsqlConnection> OpenConnectionAsync(
        CancellationToken cancellationToken = default) =>
        _dataSource.OpenConnectionAsync(cancellationToken);

    public ValueTask DisposeAsync() => _dataSource.DisposeAsync();
}
