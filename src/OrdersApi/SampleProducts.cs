namespace OrdersApi;

internal static class SampleProducts
{
    internal static readonly IReadOnlyList<SampleProduct> All = Array.AsReadOnly<SampleProduct>(
    [
        new("SKU-1001", 129.99m),
        new("SKU-1002", 349.00m),
        new("SKU-1003", 219.50m),
        new("SKU-1004", 45.75m),
        new("SKU-1005", 189.00m)
    ]);

    private static readonly Dictionary<string, decimal> Prices =
        All.ToDictionary(product => product.ProductId, product => product.Price, StringComparer.OrdinalIgnoreCase);

    internal static bool TryGetPrice(string productId, out decimal price) =>
        Prices.TryGetValue(productId, out price);
}

internal sealed record SampleProduct(string ProductId, decimal Price);
