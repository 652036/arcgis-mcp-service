using System.Text.RegularExpressions;

namespace ArcGISProMcp.AddIn.Bridge;

internal static class SecretRedactor
{
    private static readonly Regex BearerPattern = new(
        "(?i)\\bBearer\\s+[A-Za-z0-9._~+\\-/]+=*",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex NamedSecretPattern = new(
        "(?i)\\b(authorization|token|access_token|api[_-]?key|password|passwd|secret|cookie)\\b\\s*[:=]\\s*[^,;\\s]+",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex QuerySecretPattern = new(
        "(?i)([?&](?:token|access_token|api[_-]?key|sig|signature)=)[^&\\s]+",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);

    internal static string? Sanitize(string? value, int maximumLength = 2048)
    {
        if (string.IsNullOrEmpty(value))
        {
            return value;
        }

        var redacted = BearerPattern.Replace(value, "Bearer <redacted>");
        redacted = NamedSecretPattern.Replace(redacted, "$1=<redacted>");
        redacted = QuerySecretPattern.Replace(redacted, "$1<redacted>");
        return redacted.Length <= maximumLength ? redacted : redacted[..maximumLength];
    }
}
