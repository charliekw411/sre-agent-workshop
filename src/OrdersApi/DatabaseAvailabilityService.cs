using System.Diagnostics;
using Microsoft.ApplicationInsights;
using Microsoft.ApplicationInsights.DataContracts;
using Microsoft.Data.Sqlite;

namespace OrdersApi;

internal sealed class DatabaseAvailabilityService(
    OrdersRepository repository, TelemetryClient telemetry) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        await Task.Yield();
        try
        {
            using var timer = new PeriodicTimer(TimeSpan.FromMinutes(1));
            do
            {
                await CheckAsync(stoppingToken);
            }
            while (await timer.WaitForNextTickAsync(stoppingToken));
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
        }
    }

    internal async Task CheckAsync(CancellationToken cancellationToken)
    {
        using var activity = new Activity("OrdersDatabase.Availability")
            .SetIdFormat(ActivityIdFormat.W3C)
            .Start();
        var stopwatch = Stopwatch.StartNew();
        var availability = new AvailabilityTelemetry
        {
            Id = activity.SpanId.ToString(),
            Name = "orders-api-sqlite",
            Timestamp = DateTimeOffset.UtcNow,
            RunLocation = Environment.MachineName,
            Success = false
        };
        availability.Context.Operation.Id = activity.TraceId.ToString();
        availability.Context.Operation.ParentId = activity.ParentSpanId == default ? null : activity.ParentSpanId.ToString();

        try
        {
            await repository.ProbeAsync(cancellationToken);
            availability.Success = true;
            availability.Message = "Orders SQLite schema is readable.";
        }
        catch (SqliteException ex)
        {
            // The repository records the actual exception and a failed dependency before rethrowing.
            availability.Message = $"SQLite error {ex.SqliteErrorCode}/{ex.SqliteExtendedErrorCode}.";
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return;
        }

        availability.Duration = stopwatch.Elapsed;
        telemetry.TrackAvailability(availability);
    }
}
