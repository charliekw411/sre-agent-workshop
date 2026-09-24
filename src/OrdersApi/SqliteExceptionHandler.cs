using Microsoft.AspNetCore.Diagnostics;
using Microsoft.Data.Sqlite;

namespace OrdersApi;

internal sealed class SqliteExceptionHandler : IExceptionHandler
{
    public async ValueTask<bool> TryHandleAsync(
        HttpContext httpContext, Exception exception, CancellationToken cancellationToken)
    {
        if (exception is not SqliteException)
        {
            return false;
        }

        await Results.Problem(
            title: "Orders database unavailable",
            detail: "The SQLite database could not complete the operation. Check database access and available data-disk space.",
            statusCode: StatusCodes.Status503ServiceUnavailable).ExecuteAsync(httpContext);
        return true;
    }
}
