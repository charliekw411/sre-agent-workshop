using System.Collections.Concurrent;
using System.Globalization;
using Microsoft.ApplicationInsights;
using Microsoft.ApplicationInsights.AspNetCore.Extensions;
using Microsoft.ApplicationInsights.Channel;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.ApplicationInsights.Extensibility;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using Testcontainers.PostgreSql;

namespace OrdersApi.Tests;

[CollectionDefinition(Name, DisableParallelization = true)]
public sealed class PostgreSqlCollection : ICollectionFixture<PostgreSqlServerFixture>
{
    public const string Name = "PostgreSQL";
}

public sealed class PostgreSqlServerFixture : IAsyncLifetime
{
    public const string ConnectionStringEnvironment =
        "ORDERS_TEST_POSTGRES_CONNECTION_STRING";
    private PostgreSqlContainer? _container;
    private string? _adminConnectionString;

    public async Task InitializeAsync()
    {
        _adminConnectionString = Environment.GetEnvironmentVariable(
            ConnectionStringEnvironment);
        if (string.IsNullOrWhiteSpace(_adminConnectionString))
        {
            _container = new PostgreSqlBuilder("postgres:16-alpine")
                .WithDatabase("postgres")
                .WithUsername("postgres")
                .WithPassword($"test-{Guid.NewGuid():N}")
                .Build();
            await _container.StartAsync();
            _adminConnectionString = _container.GetConnectionString();
        }

        var builder = AdminConnectionString();
        await using var connection = new NpgsqlConnection(builder.ConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "SHOW server_version_num;";
        var version = Convert.ToInt32(await command.ExecuteScalarAsync());
        if (version / 10_000 != 16)
        {
            throw new InvalidOperationException(
                $"{ConnectionStringEnvironment} must target PostgreSQL 16.");
        }
    }

    public async Task DisposeAsync()
    {
        if (_container is not null)
        {
            await _container.DisposeAsync();
        }
    }

    internal async Task<TestDatabase> CreateDatabaseAsync()
    {
        var databaseName = $"orders_test_{Guid.NewGuid():N}";
        var admin = AdminConnectionString();
        await using (var connection = new NpgsqlConnection(admin.ConnectionString))
        {
            await connection.OpenAsync();
            await using var command = connection.CreateCommand();
            command.CommandText = $"CREATE DATABASE \"{databaseName}\";";
            await command.ExecuteNonQueryAsync();
        }

        var database = new NpgsqlConnectionStringBuilder(admin.ConnectionString)
        {
            Database = databaseName,
            Pooling = true,
            Timeout = OrdersDatabase.CommandTimeoutSeconds,
            CommandTimeout = OrdersDatabase.CommandTimeoutSeconds,
            ApplicationName = "orders-api-tests"
        };
        return new TestDatabase(
            this, databaseName, database.ConnectionString);
    }

    internal async Task DropDatabaseAsync(string databaseName)
    {
        if (!databaseName.StartsWith("orders_test_", StringComparison.Ordinal)
            || databaseName.Any(character =>
                !char.IsAsciiLetterOrDigit(character) && character != '_'))
        {
            throw new InvalidOperationException("Refusing to drop an unexpected database.");
        }

        var admin = AdminConnectionString();
        await using var connection = new NpgsqlConnection(admin.ConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = $"DROP DATABASE IF EXISTS \"{databaseName}\" WITH (FORCE);";
        await command.ExecuteNonQueryAsync();
    }

    private NpgsqlConnectionStringBuilder AdminConnectionString()
    {
        if (string.IsNullOrWhiteSpace(_adminConnectionString))
        {
            throw new InvalidOperationException("PostgreSQL test fixture is not initialized.");
        }

        NpgsqlConnectionStringBuilder builder;
        try
        {
            builder = new NpgsqlConnectionStringBuilder(_adminConnectionString);
        }
        catch (ArgumentException ex)
        {
            throw new InvalidOperationException(
                $"{ConnectionStringEnvironment} is not a valid PostgreSQL connection string.",
                ex);
        }

        if (string.IsNullOrWhiteSpace(builder.Host)
            || string.IsNullOrWhiteSpace(builder.Database)
            || string.IsNullOrWhiteSpace(builder.Username)
            || string.IsNullOrEmpty(builder.Password))
        {
            throw new InvalidOperationException(
                $"{ConnectionStringEnvironment} must include Host, Database, Username, and Password.");
        }

        builder.Pooling = false;
        builder.Timeout = OrdersDatabase.CommandTimeoutSeconds;
        builder.CommandTimeout = OrdersDatabase.CommandTimeoutSeconds;
        return builder;
    }
}

internal sealed class TestDatabase : IAsyncDisposable
{
    private readonly PostgreSqlServerFixture _server;
    private readonly string _databaseName;

    internal TestDatabase(
        PostgreSqlServerFixture server, string databaseName, string connectionString)
    {
        _server = server;
        _databaseName = databaseName;
        ConnectionString = connectionString;
        Database = new OrdersDatabase(
            connectionString, OrdersDatabase.PasswordAuthentication);
    }

    public string ConnectionString { get; }
    public OrdersDatabase Database { get; }

    public Task BootstrapAsync() =>
        DatabaseBootstrap.InitializeAsync(Database, CancellationToken.None);

    public async Task ExecuteAsync(string sql)
    {
        await using var connection = await Database.OpenConnectionAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync();
    }

    public async Task<T> ExecuteScalarAsync<T>(string sql)
    {
        await using var connection = await Database.OpenConnectionAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        var value = await command.ExecuteScalarAsync()
            ?? throw new InvalidOperationException("PostgreSQL returned a null scalar.");
        return (T)Convert.ChangeType(value, typeof(T), CultureInfo.InvariantCulture);
    }

    public async ValueTask DisposeAsync()
    {
        await Database.DisposeAsync();
        await _server.DropDatabaseAsync(_databaseName);
    }
}

internal sealed class RecordingTelemetryChannel : ITelemetryChannel
{
    public const string ConnectionString =
        "InstrumentationKey=00000000-0000-0000-0000-000000000001;IngestionEndpoint=http://127.0.0.1:1/";

    public ConcurrentQueue<ITelemetry> Items { get; } = new();
    public bool? DeveloperMode { get; set; }
    public string EndpointAddress { get; set; } = "http://127.0.0.1:1/";
    public void Send(ITelemetry item) => Items.Enqueue(item.DeepClone());
    public void Flush() { }
    public void Dispose() { }

    public async Task<T> WaitForAsync<T>(Func<T, bool> predicate) where T : ITelemetry
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        while (true)
        {
            var match = Items.OfType<T>().FirstOrDefault(predicate);
            if (match is not null)
            {
                return match;
            }
            await Task.Delay(10, timeout.Token);
        }
    }
}

internal sealed class RepositoryFixture : IAsyncDisposable
{
    private readonly TelemetryConfiguration _configuration =
        TelemetryConfiguration.CreateDefault();

    private RepositoryFixture(TestDatabase store)
    {
        Store = store;
        _configuration.ConnectionString = RecordingTelemetryChannel.ConnectionString;
        _configuration.TelemetryChannel = Channel;
        _configuration.TelemetryInitializers.Add(
            new CloudRoleNameInitializer("orders-api"));
        _configuration.TelemetryInitializers.Add(
            new OperationCorrelationTelemetryInitializer());
        Telemetry = new TelemetryClient(_configuration);
        Repository = new OrdersRepository(
            Store.Database, Telemetry, NullLogger<OrdersRepository>.Instance);
    }

    public TestDatabase Store { get; }
    public RecordingTelemetryChannel Channel { get; } = new();
    public TelemetryClient Telemetry { get; }
    public OrdersRepository Repository { get; }

    public static async Task<RepositoryFixture> CreateAsync(
        PostgreSqlServerFixture server) =>
        new(await server.CreateDatabaseAsync());

    public async ValueTask DisposeAsync()
    {
        _configuration.Dispose();
        await Store.DisposeAsync();
    }
}

internal sealed class TestApplication(
    WebApplication application,
    RecordingTelemetryChannel channel) : IAsyncDisposable
{
    public HttpClient Client { get; } = application.GetTestClient();
    public RecordingTelemetryChannel Channel { get; } = channel;

    public static async Task<TestApplication> StartAsync(TestDatabase store)
    {
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions
        {
            ApplicationName = typeof(Program).Assembly.FullName,
            EnvironmentName = Environments.Production,
            ContentRootPath = AppContext.BaseDirectory,
            WebRootPath = Path.GetFullPath(Path.Combine(
                AppContext.BaseDirectory, "..", "..", "..", "..", "OrdersApi", "wwwroot"))
        });
        builder.Configuration.Sources.Clear();
        builder.Configuration.AddInMemoryCollection(new Dictionary<string, string?>
        {
            ["ConnectionStrings:OrdersDb"] = store.ConnectionString,
            ["OrdersDatabase:Authentication"] = OrdersDatabase.PasswordAuthentication,
            ["APPLICATIONINSIGHTS_CONNECTION_STRING"] =
                RecordingTelemetryChannel.ConnectionString,
            ["ApplicationInsights:ConnectionString"] =
                RecordingTelemetryChannel.ConnectionString,
            ["SERVICE_NAME"] = "orders-api"
        });
        builder.Logging.ClearProviders();
        builder.WebHost.UseTestServer();
        var channel = new RecordingTelemetryChannel();
        builder.Services.AddSingleton<ITelemetryChannel>(channel);
        builder.Services.Configure<ApplicationInsightsServiceOptions>(options =>
        {
            options.EnableAdaptiveSampling = false;
            options.EnableQuickPulseMetricStream = false;
            options.EnablePerformanceCounterCollectionModule = false;
            options.EnableEventCounterCollectionModule = false;
            options.EnableDiagnosticsTelemetryModule = false;
            options.EnableAppServicesHeartbeatTelemetryModule = false;
            options.EnableAzureInstanceMetadataTelemetryModule = false;
        });
        var application = Program.CreateApplication(builder);
        try
        {
            await application.StartAsync();
            await channel.WaitForAsync<AvailabilityTelemetry>(
                item => item.Name == "orders-api-postgresql");
            return new TestApplication(application, channel);
        }
        catch
        {
            await application.DisposeAsync();
            throw;
        }
    }

    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await application.StopAsync();
        await application.DisposeAsync();
    }
}
