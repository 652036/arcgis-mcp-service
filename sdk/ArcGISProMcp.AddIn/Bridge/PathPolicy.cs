namespace ArcGISProMcp.AddIn.Bridge;

internal static class PathPolicy
{
    internal static IReadOnlyList<string> ParseRoots(string? value, string settingName)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return Array.Empty<string>();
        }

        return value
            .Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Select(item => NormalizeConfiguredRoot(item.Trim('"'), settingName))
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();
    }

    internal static string? ParseOptionalRoot(string? value, string settingName) =>
        string.IsNullOrWhiteSpace(value) ? null : NormalizeConfiguredRoot(value.Trim().Trim('"'), settingName);

    internal static string ValidateInput(string value, IReadOnlyList<string> roots, string label)
    {
        if (roots.Count == 0)
        {
            throw new ApiException(
                503,
                "input_roots_required",
                "SDK geoprocessing requires ARCGIS_PRO_MCP_INPUT_ROOTS.");
        }

        var path = NormalizeAbsoluteForRequest(value, label);
        RequireUnderAnyRoot(path, roots, label, "ARCGIS_PRO_MCP_INPUT_ROOTS");
        return path;
    }

    internal static string ValidateOutput(string value, string? outputRoot, string label)
    {
        if (string.IsNullOrWhiteSpace(outputRoot))
        {
            throw new ApiException(
                503,
                "gp_output_root_required",
                "SDK geoprocessing requires ARCGIS_PRO_MCP_GP_OUTPUT_ROOT.");
        }

        var path = NormalizeAbsoluteForRequest(value, label);
        if (ContainsExternalConnectionFile(path))
        {
            throw new ApiException(
                403,
                "external_output_forbidden",
                $"{label} must not target an enterprise or server connection file.");
        }

        RequireUnderAnyRoot(path, new[] { outputRoot }, label, "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT");
        return path;
    }

    internal static void ValidateProject(string value, IReadOnlyList<string> roots)
    {
        if (roots.Count == 0)
        {
            return;
        }

        var path = NormalizeAbsoluteForRequest(value, "expectedProjectUri");
        RequireUnderAnyRoot(path, roots, "expectedProjectUri", "ARCGIS_PRO_MCP_PROJECT_ROOTS");
    }

    private static string NormalizeAbsoluteForRequest(string value, string label)
    {
        try
        {
            return NormalizeAbsolute(value.Trim().Trim('"'), label);
        }
        catch (Exception ex) when (ex is InvalidOperationException or ArgumentException or
                                   NotSupportedException or PathTooLongException or IOException or
                                   UnauthorizedAccessException)
        {
            throw new ApiException(400, "absolute_path_required", $"{label} must be an absolute local path.");
        }
    }

    private static string NormalizeAbsolute(string value, string settingName)
    {
        if (string.IsNullOrWhiteSpace(value) || !Path.IsPathFullyQualified(value))
        {
            throw new InvalidOperationException($"{settingName} must contain absolute local paths.");
        }

        if (value.StartsWith("\\\\?\\", StringComparison.Ordinal) ||
            value.StartsWith("\\\\.\\", StringComparison.Ordinal))
        {
            throw new InvalidOperationException($"{settingName} must not use a device path.");
        }

        var fullPath = ResolveExistingLinks(Path.GetFullPath(value));
        var pathRoot = Path.GetPathRoot(fullPath);
        return string.Equals(fullPath, pathRoot, StringComparison.OrdinalIgnoreCase)
            ? fullPath
            : fullPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }

    private static string NormalizeConfiguredRoot(string value, string settingName)
    {
        var root = NormalizeAbsolute(value, settingName);
        if (!Directory.Exists(root))
        {
            throw new InvalidOperationException($"{settingName} contains a directory that does not exist.");
        }

        return root;
    }

    private static string ResolveExistingLinks(string fullPath)
    {
        var root = Path.GetPathRoot(fullPath) ?? throw new InvalidOperationException("Path root is unavailable.");
        var parts = fullPath[root.Length..]
            .Split(new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries);
        var current = root;

        for (var index = 0; index < parts.Length; index++)
        {
            var candidate = Path.Combine(current, parts[index]);
            FileSystemInfo? info = Directory.Exists(candidate)
                ? new DirectoryInfo(candidate)
                : File.Exists(candidate)
                    ? new FileInfo(candidate)
                    : null;
            if (info is null)
            {
                current = Path.Combine(current, Path.Combine(parts[index..]));
                break;
            }

            if ((info.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                current = info.ResolveLinkTarget(returnFinalTarget: true)?.FullName ?? candidate;
            }
            else
            {
                current = candidate;
            }
        }

        return Path.GetFullPath(current);
    }

    private static void RequireUnderAnyRoot(
        string path,
        IReadOnlyList<string> roots,
        string label,
        string settingName)
    {
        if (roots.Any(root => IsUnderRoot(path, root)))
        {
            return;
        }

        throw new ApiException(403, "path_outside_allowed_roots", $"{label} must be under {settingName}.");
    }

    private static bool IsUnderRoot(string path, string root)
    {
        var relative = Path.GetRelativePath(root, path);
        return !Path.IsPathRooted(relative) &&
               !string.Equals(relative, "..", StringComparison.Ordinal) &&
               !relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal) &&
               !relative.StartsWith(".." + Path.AltDirectorySeparatorChar, StringComparison.Ordinal);
    }

    private static bool ContainsExternalConnectionFile(string path) =>
        path.Split(new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar },
                StringSplitOptions.RemoveEmptyEntries)
            .Any(part => part.EndsWith(".sde", StringComparison.OrdinalIgnoreCase) ||
                         part.EndsWith(".ags", StringComparison.OrdinalIgnoreCase));
}
