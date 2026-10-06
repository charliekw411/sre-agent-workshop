using Microsoft.AspNetCore.Diagnostics;
using Npgsql;

namespace OrdersApi;

internal sealed class PostgreSqlExceptionHandler : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(
        HttpContext httpContext, Exception exception, CancellationToken cancellationToken)
    {
        if (exception is not NpgsqlException
            and not TimeoutException
            and not OrdersDatabaseUnavailableException)
        {
            return false;
        }

        await Results.Problem(
            title: "Orders database unavailable",
            detail: "PostgreSQL could not complete the operation. Check database readiness and private connectivity.",
            statusCode: StatusCodes.Status503ServiceUnavailable).ExecuteAsync(httpContext);
        return true;
    }
}

internal sealed class OrdersDatabaseUnavailableException(string message) : Exception(message);

internal sealed class DatabaseOperationException(string operation, string resultCode, bool transient)
    : Exception($"PostgreSQL operation {operation} failed ({resultCode}; transient={transient}).");
