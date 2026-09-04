namespace ArcGISProMcp.AddIn.Bridge;

internal sealed class BridgeOptions
{
    internal const string ProtocolVersion = "1";

    internal bool AllowWrite { get; private init; }
    internal int EventCapacity { get; private init; } = 512;
    internal int MaxBodyBytes { get; private init; } = 1_048_576;
    internal int MaxHeaderBytes { get; private init; } = 16_384;
    internal int MaxConcurrentClients { get; private init; } = 24;
    internal TimeSpan RequestReadTimeout { get; private init; } = TimeSpan.FromSeconds(10);
    internal TimeSpan LeaseTtl { get; private init; } = TimeSpan.FromSeconds(45);
    internal string DiscoveryDirectory { get; private init; } = string.Empty;
    internal string? GpOutputRoot { get; private init; }
    internal IReadOnlyList<string> InputRoots { get; private init; } = Array.Empty<string>();
    internal IReadOnlyList<string> ProjectRoots { get; private init; } = Array.Empty<string>();
    internal bool AllowEditCommands { get; private init; }
    internal bool AllowDiscardEdits { get; private init; }
    internal bool AllowFeatureEdits { get; private init; }
    internal bool AllowDestructive { get; private init; }
    internal IReadOnlySet<string> GpToolAllowlist { get; private init; } =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase);
    internal IReadOnlySet<string> GpEnvironmentAllowlist { get; private init; } =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase);

    internal static BridgeOptions FromEnvironment()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (string.IsNullOrWhiteSpace(localAppData))
        {
            throw new InvalidOperationException("Local application data directory is unavailable.");
        }

        var inputRoots = PathPolicy.ParseRoots(
            Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_INPUT_ROOTS"),
            "ARCGIS_PRO_MCP_INPUT_ROOTS");
        var configuredProjectRoots = PathPolicy.ParseRoots(
            Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_PROJECT_ROOTS"),
            "ARCGIS_PRO_MCP_PROJECT_ROOTS");
        var outputRoot = PathPolicy.ParseOptionalRoot(
            Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_GP_OUTPUT_ROOT"),
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT");

        return new BridgeOptions
        {
            AllowWrite = IsEnabled(
                Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_ALLOW_WRITE"),
                defaultValue: true),
            AllowEditCommands = IsEnabled(
                Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS")),
            AllowDiscardEdits = IsEnabled(
                Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS")),
            AllowFeatureEdits = IsEnabled(
                Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS")),
            AllowDestructive = IsEnabled(
                Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE")),
            DiscoveryDirectory = Path.Combine(localAppData, "ArcGISProMcp", "sdk-bridge"),
            GpOutputRoot = outputRoot,
            InputRoots = inputRoots,
            ProjectRoots = configuredProjectRoots.Count > 0 ? configuredProjectRoots : inputRoots,
            GpToolAllowlist = ParseSet(Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST")),
            GpEnvironmentAllowlist = ParseSet(
                Environment.GetEnvironmentVariable("ARCGIS_PRO_MCP_SDK_GP_ENV_ALLOWLIST")),
        };
    }

    private static bool IsEnabled(string? value, bool defaultValue = false)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return defaultValue;
        }

        return string.Equals(value, "1", StringComparison.Ordinal) ||
               string.Equals(value, "true", StringComparison.OrdinalIgnoreCase) ||
               string.Equals(value, "yes", StringComparison.OrdinalIgnoreCase) ||
               string.Equals(value, "on", StringComparison.OrdinalIgnoreCase);
    }

    private static IReadOnlySet<string> ParseSet(string? value) =>
        new HashSet<string>(
            (value ?? string.Empty)
                .Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Where(item => item.Length <= 256),
            StringComparer.OrdinalIgnoreCase);
}
