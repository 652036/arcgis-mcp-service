using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using ArcGISProMcp.AddIn.Events;

namespace ArcGISProMcp.AddIn.Bridge;

internal static class ProContext
{
    internal static Task<string> CaptureProjectUriAsync() =>
        QueuedTask.Run(() => ProjectIdentity.Canonicalize(Project.Current?.URI));

    internal static async Task<ProContextSnapshot> CaptureAsync(ProEventController events)
    {
        for (var attempt = 0; attempt < 2; attempt++)
        {
            var generations = events.Generations;
            var captured = await QueuedTask.Run(() => Capture(generations)).ConfigureAwait(false);
            if (generations == events.Generations)
            {
                return captured;
            }
        }

        throw new ApiException(409, "context_busy", "The active ArcGIS Pro context changed; retry the snapshot.");
    }

    private static ProContextSnapshot Capture(ContextGenerations generations)
    {
        var project = Project.Current;
        var activeTablePane = FrameworkApplication.Panes.ActivePane as ITablePane;
        var activeTableMember = activeTablePane?.MapMember;
        var layoutView = LayoutView.Active;
        var mapView = layoutView?.ActivatedMapView ?? MapView.Active;
        var map = mapView?.Map ?? activeTableMember?.Map;
        var selectedLayers = mapView?.GetSelectedLayers() ?? Array.Empty<Layer>();
        var activeLayer = selectedLayers.FirstOrDefault();
        var selectionLayers = new List<LayerSelectionSnapshot>();
        if (map is not null)
        {
            foreach (var pair in map.GetSelection().ToDictionary<MapMember>()
                         .Where(pair => pair.Value.Count > 0)
                         .OrderBy(pair => pair.Key.URI, StringComparer.Ordinal))
            {
                var ids = pair.Value.OrderBy(value => value).ToArray();
                selectionLayers.Add(new LayerSelectionSnapshot(
                    pair.Key.URI,
                    pair.Key.Name,
                    ids.Length,
                    OidSelectionDigest.ComputeLayer(pair.Key.URI, ids)));
            }
        }

        var camera = mapView?.Camera;
        var time = mapView?.Time;
        var totalSelected = selectionLayers.Sum(item => item.Count);
        var activeViewType = activeTablePane is not null
            ? "table"
            : layoutView is not null
                ? "layout"
                : mapView is not null
                    ? "map"
                    : "none";
        var activeViewUri = activeTableMember?.URI ?? layoutView?.Layout.URI ?? map?.URI;
        return new ProContextSnapshot(
            ProjectIdentity.Canonicalize(project?.URI),
            project?.Name ?? string.Empty,
            project?.ReadOnly ?? true,
            activeViewType,
            activeViewUri,
            map?.Name,
            map?.URI,
            camera is null
                ? null
                : new CameraSnapshot(
                    camera.X,
                    camera.Y,
                    camera.Z,
                    camera.Scale,
                    camera.Heading,
                    camera.Pitch,
                    camera.Roll,
                    camera.SpatialReference?.Wkid ?? 0,
                    camera.SpatialReference?.Name),
            activeLayer is null
                ? null
                : new ActiveLayerSnapshot(activeLayer.URI, activeLayer.Name, activeLayer.GetType().Name),
            selectedLayers.Count,
            new SelectionSnapshot(
                totalSelected,
                OidSelectionDigest.ComputeAggregate(selectionLayers),
                selectionLayers),
            time is null
                ? null
                : new TimeRangeSnapshot(FormatTime(time.Start), FormatTime(time.End)),
            generations.ContextGeneration,
            generations.SelectionGeneration,
            generations.EditGeneration,
            generations.DrawGeneration);
    }

    private static string? FormatTime(DateTime? value) =>
        value?.ToUniversalTime().ToString("O", System.Globalization.CultureInfo.InvariantCulture);
}

internal static class OidSelectionDigest
{
    internal static string ComputeLayer(string layerUri, IEnumerable<long> objectIds)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        Append(hash, layerUri);
        hash.AppendData(new byte[] { 0 });
        foreach (var objectId in objectIds.OrderBy(value => value))
        {
            Append(hash, objectId.ToString(System.Globalization.CultureInfo.InvariantCulture));
            hash.AppendData(new byte[] { (byte)'\n' });
        }

        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    internal static string ComputeAggregate(IEnumerable<LayerSelectionSnapshot> layers)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        foreach (var layer in layers.OrderBy(item => item.LayerUri, StringComparer.Ordinal))
        {
            Append(hash, layer.LayerUri);
            hash.AppendData(new byte[] { 0 });
            Append(hash, layer.OidDigest);
            hash.AppendData(new byte[] { (byte)'\n' });
        }

        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }

    private static void Append(IncrementalHash hash, string value) =>
        hash.AppendData(Encoding.UTF8.GetBytes(value));
}

internal static class ProjectIdentity
{
    internal static string Canonicalize(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return string.Empty;
        }

        var trimmed = value.Trim();
        if (Uri.TryCreate(trimmed, UriKind.Absolute, out var uri) &&
            (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps))
        {
            if (!string.IsNullOrEmpty(uri.UserInfo))
            {
                throw new ApiException(400, "project_uri_userinfo_forbidden", "Project URI must not contain credentials.");
            }

            var builder = new UriBuilder(uri)
            {
                Query = string.Empty,
                Fragment = string.Empty,
                UserName = string.Empty,
                Password = string.Empty,
            };
            return builder.Uri.AbsoluteUri.TrimEnd('/');
        }

        try
        {
            return Path.GetFullPath(trimmed).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            throw new ApiException(400, "invalid_project_uri", "Project URI is not a valid local path or HTTP(S) URI.");
        }
    }

    internal static bool Equals(string left, string right) =>
        string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
}
