using System.Collections.Concurrent;

namespace OrdersApi;

public enum StorageFaultPhase
{
    Idle,
    Running,
    Completed,
    Failed
}

/// <summary>
/// Holds the currently active fault-injection state. All faults are bounded: CPU load stops at
/// <see cref="CpuLoadUntilUtc"/>, error injection stops at <see cref="ErrorInjectionUntilUtc"/>, and
/// the storage fill stops when the database reaches its target or when a reset is requested.
/// </summary>
public sealed class FaultState
{
    private readonly ConcurrentDictionary<string, string> _notes = new();

    public DateTimeOffset? CpuLoadUntilUtc { get; private set; }

    public int CpuLoadThreads { get; private set; }

    public DateTimeOffset? ErrorInjectionUntilUtc { get; private set; }

    public int ErrorRatePercent { get; private set; }

    public StorageFaultPhase StoragePhase { get; private set; } = StorageFaultPhase.Idle;

    public long StorageBytesWritten { get; private set; }

    public int StorageTargetPercent { get; private set; }

    public string? StorageMessage { get; private set; }

    public CancellationTokenSource? StorageCancellation { get; private set; }

    public bool CpuLoadActive => CpuLoadUntilUtc > DateTimeOffset.UtcNow;

    public bool ErrorInjectionActive => ErrorInjectionUntilUtc > DateTimeOffset.UtcNow && ErrorRatePercent > 0;

    public IReadOnlyDictionary<string, string> Notes => _notes;

    public void StartCpuLoad(int seconds, int threads)
    {
        CpuLoadUntilUtc = DateTimeOffset.UtcNow.AddSeconds(seconds);
        CpuLoadThreads = threads;
        _notes["cpu"] = $"{threads} thread(s) for {seconds}s";
    }

    public void StartErrorInjection(int ratePercent, int ttlSeconds)
    {
        ErrorRatePercent = ratePercent;
        ErrorInjectionUntilUtc = DateTimeOffset.UtcNow.AddSeconds(ttlSeconds);
        _notes["errors"] = $"{ratePercent}% for {ttlSeconds}s";
    }

    public CancellationToken StartStorageFill(int targetPercent)
    {
        StorageCancellation?.Cancel();
        StorageCancellation = new CancellationTokenSource();
        StoragePhase = StorageFaultPhase.Running;
        StorageTargetPercent = targetPercent;
        StorageBytesWritten = 0;
        StorageMessage = "Fill in progress.";
        _notes["storage"] = $"target {targetPercent}% of max size";
        return StorageCancellation.Token;
    }

    public void RecordStorageProgress(long bytesWritten) => StorageBytesWritten = bytesWritten;

    public void CompleteStorageFill(StorageFaultPhase phase, string message)
    {
        StoragePhase = phase;
        StorageMessage = message;
    }

    public void Reset()
    {
        CpuLoadUntilUtc = null;
        CpuLoadThreads = 0;
        ErrorInjectionUntilUtc = null;
        ErrorRatePercent = 0;
        StorageCancellation?.Cancel();
        StorageCancellation = null;
        StoragePhase = StorageFaultPhase.Idle;
        StorageBytesWritten = 0;
        StorageTargetPercent = 0;
        StorageMessage = null;
        _notes.Clear();
    }
}
