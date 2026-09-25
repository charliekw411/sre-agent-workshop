using System.Collections.Concurrent;
using Microsoft.ApplicationInsights;
using Microsoft.ApplicationInsights.AspNetCore.Extensions;
using Microsoft.ApplicationInsights.Channel;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.ApplicationInsights.Extensibility;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Abstractions;

namespace OrdersApi.Tests;

internal sealed class TestDatabase : IDisposable
{
    private readonly string _directory = Path.Combine(AppContext.BaseDirectory, "test-data", Guid.NewGuid().ToString("N"));

    public TestDatabase()
    {
        Directory.CreateDirectory(_directory);
        ConnectionString = new SqliteConnectionStringBuilder
        {
            DataSource = Path.Combine(_directory, "orders.db")
        }.ConnectionString;
        Database = new OrdersDatabase(ConnectionString);
    }

    public string ConnectionString { get; }
    public OrdersDatabase Database { get; }

    public Task BootstrapAsync() => DatabaseBootstrap.InitializeAsync(Database, CancellationToken.None);

    public async Task ExecuteAsync(string sql)
    {
        await using var connection = Database.CreateConnection();
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync();
    }

    public void Dispose() => Directory.Delete(_directory, recursive: true);
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
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
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

internal sealed class RepositoryFixture : IDisposable
{
    private readonly TelemetryConfiguration _configuration = TelemetryConfiguration.CreateDefault();

    public RepositoryFixture()
    {
        _configuration.ConnectionString = RecordingTelemetryChannel.ConnectionString;
        _configuration.TelemetryChannel = Channel;
        _configuration.TelemetryInitializers.Add(new CloudRoleNameInitializer("orders-api"));
        _configuration.TelemetryInitializers.Add(new OperationCorrelationTelemetryInitializer());
        Telemetry = new TelemetryClient(_configuration);
        Repository = new OrdersRepository(Store.Database, Telemetry, NullLogger<OrdersRepository>.Instance);
    }

    public TestDatabase Store { get; } = new();
    public RecordingTelemetryChannel Channel { get; } = new();
    public TelemetryClient Telemetry { get; }
    public OrdersRepository Repository { get; }

    public void Dispose()
    {
        _configuration.Dispose();
        Store.Dispose();
    }
}

internal sealed class TestApplication(WebApplication application, RecordingTelemetryChannel channel) : IAsyncDisposable
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
            ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = RecordingTelemetryChannel.ConnectionString,
            ["ApplicationInsights:ConnectionString"] = RecordingTelemetryChannel.ConnectionString,
            ["SERVICE_NAME"] = "orders-api",
            ["Fault:Enabled"] = "true",
            ["Fault:Token"] = "obsolete-token"
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
            await channel.WaitForAsync<AvailabilityTelemetry>(item => item.Name == "orders-api-sqlite");
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
