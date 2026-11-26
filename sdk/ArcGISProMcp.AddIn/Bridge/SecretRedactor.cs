using System.Text.RegularExpressions;

namespace ArcGISProMcp.AddIn.Bridge;

internal static class SecretRedactor
{
    private const string LongKeyToken =
        "authorization|password(?:[_-]?utf8)?|passwd|secret|token|cookie|credential|signature|" +
        "api[_-]?key|access[_-]?key|account[_-]?key|private[_-]?key|subscription[_-]?key";
    private const string ShortKeyToken = "pwd|sig";
    private const string KeyName =
        "(?:[A-Za-z0-9_-]*?(?:" + LongKeyToken + ")|(?:[A-Za-z0-9_-]+[_-])?(?:" + ShortKeyToken + "))";

    private static readonly Regex UrlUserInfoPattern = new(
        "(?i)(\\b[a-z][a-z0-9+.-]*://)[^/@\\s]+@",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex BearerPattern = new(
        "(?i)\\b(Bearer|Basic)\\s+[A-Za-z0-9._~+\\-/]+=*",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex NamedSecretPattern = new(
        "(?i)(\"" + KeyName + "\"|'" + KeyName + "'|(?<![A-Za-z0-9_-])" + KeyName + "\\b)\\s*[:=]\\s*(?:\"[^\"]*\"|'[^']*'|[^,;\\s&}\\]]+)",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);
    private static readonly Regex QuerySecretPattern = new(
        "(?i)([?&]" + KeyName + "=)[^&#\\s]*",
        RegexOptions.CultureInvariant | RegexOptions.Compiled);

    internal static string? Sanitize(string? value, int maximumLength = 2048)
    {
        if (string.IsNullOrEmpty(value))
        {
            return value;
        }

        var redacted = UrlUserInfoPattern.Replace(value, "$1<redacted>@");
        redacted = BearerPattern.Replace(redacted, "$1 <redacted>");
        redacted = QuerySecretPattern.Replace(redacted, "$1<redacted>");
        redacted = NamedSecretPattern.Replace(redacted, "$1=<redacted>");
        return redacted.Length <= maximumLength ? redacted : redacted[..maximumLength];
    }
}
