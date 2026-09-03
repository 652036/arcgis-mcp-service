namespace ArcGISProMcp.AddIn.Bridge;

internal static class SecurityTokens
{
    internal static string Create(int bytes = 32)
    {
        var token = Convert.ToBase64String(RandomNumberGenerator.GetBytes(bytes));
        return token.TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }

    internal static bool FixedTimeEquals(string? left, string? right)
    {
        if (left is null || right is null)
        {
            return false;
        }

        var leftBytes = Encoding.UTF8.GetBytes(left);
        var rightBytes = Encoding.UTF8.GetBytes(right);
        return leftBytes.Length == rightBytes.Length &&
               CryptographicOperations.FixedTimeEquals(leftBytes, rightBytes);
    }
}
