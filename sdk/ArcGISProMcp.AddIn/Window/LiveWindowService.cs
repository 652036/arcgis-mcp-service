using System.Globalization;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using ArcGISProMcp.AddIn.Bridge;
using ArcGISProMcp.AddIn.Events;

namespace ArcGISProMcp.AddIn.Window;

internal sealed class LiveWindowService
{
    private readonly ProEventController _events;

    internal LiveWindowService(ProEventController events)
    {
        _events = events;
    }

    internal async Task<ViewMutationSnapshot> SetCameraAsync(SetCameraRequest request)
    {
        ValidateViewRequest(request);
        if (request.X is null && request.Y is null && request.Z is null && request.Scale is null &&
            request.Heading is null && request.Pitch is null && request.Roll is null)
        {
            throw new ApiException(400, "camera_value_required", "At least one typed camera value is required.");
        }

        if (request.ExpectedSpatialReferenceWkid is null or < 0)
        {
            throw new ApiException(
                400,
                "expected_spatial_reference_required",
                "expectedSpatialReferenceWkid from the latest camera snapshot is required.");
        }

        ValidateFinite(request.X, "x");
        ValidateFinite(request.Y, "y");
        ValidateFinite(request.Z, "z");
        ValidateFinite(request.Scale, "scale");
        ValidateFinite(request.Heading, "heading");
        ValidateFinite(request.Pitch, "pitch");
        ValidateFinite(request.Roll, "roll");
        if (request.Scale is <= 0)
        {
            throw new ApiException(400, "invalid_camera_scale", "scale must be greater than zero.");
        }

        if (request.Pitch is < -90 or > 90)
        {
            throw new ApiException(400, "invalid_camera_pitch", "pitch must be between -90 and 90 degrees.");
        }

        var duration = ValidateDuration(request.DurationMilliseconds);
        var completed = await QueuedTask.Run(() =>
        {
            var view = RequireActiveMap(request);
            var camera = view.Camera;
            if (camera.SpatialReference?.Wkid != request.ExpectedSpatialReferenceWkid)
            {
                throw new ApiException(
                    409,
                    "spatial_reference_changed",
                    "The active camera spatial reference changed; refresh context before retrying.");
            }

            camera.X = request.X ?? camera.X;
            camera.Y = request.Y ?? camera.Y;
            camera.Z = request.Z ?? camera.Z;
            camera.Scale = request.Scale ?? camera.Scale;
            camera.Heading = NormalizeDegrees(request.Heading ?? camera.Heading);
            camera.Pitch = request.Pitch ?? camera.Pitch;
            camera.Roll = NormalizeSignedDegrees(request.Roll ?? camera.Roll);
            RequireGeneration(request.ExpectedContextGeneration!.Value);
            return view.ZoomTo(camera, duration);
        }).ConfigureAwait(false);

        RequireSameMap(request.ExpectedMapUri);
        return new ViewMutationSnapshot(
            completed,
            null,
            await ProContext.CaptureAsync(_events).ConfigureAwait(false));
    }

    internal async Task<ViewMutationSnapshot> ZoomLayerAsync(ZoomLayerRequest request)
    {
        ValidateViewRequest(request);
        if (string.IsNullOrWhiteSpace(request.LayerUri) || request.LayerUri.Length > 4096)
        {
            throw new ApiException(400, "layer_uri_required", "layerUri is required and must be bounded.");
        }

        var duration = ValidateDuration(request.DurationMilliseconds);
        var completed = await QueuedTask.Run(() =>
        {
            var view = RequireActiveMap(request);
            var matches = view.Map.GetLayersAsFlattenedList()
                .Where(layer => string.Equals(layer.URI, request.LayerUri, StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
            {
                throw new ApiException(
                    matches.Length == 0 ? 404 : 409,
                    matches.Length == 0 ? "layer_not_found" : "layer_uri_ambiguous",
                    "layerUri must identify exactly one layer in the active map.");
            }

            RequireGeneration(request.ExpectedContextGeneration!.Value);
            return view.ZoomTo(
                matches[0],
                request.SelectedOnly,
                duration,
                request.MaintainViewDirection);
        }).ConfigureAwait(false);

        RequireSameMap(request.ExpectedMapUri);
        return new ViewMutationSnapshot(
            completed,
            null,
            await ProContext.CaptureAsync(_events).ConfigureAwait(false));
    }

    internal async Task<ViewMutationSnapshot> RefreshAsync(
        RefreshViewRequest request,
        CancellationToken cancellationToken)
    {
        ValidateViewRequest(request);
        if (request.WaitMilliseconds is < 1 or > 30_000)
        {
            throw new ApiException(400, "invalid_draw_wait", "waitMilliseconds must be between 1 and 30000.");
        }

        var afterDrawGeneration = await QueuedTask.Run(() =>
        {
            var view = RequireActiveMap(request);
            RequireGeneration(request.ExpectedContextGeneration!.Value);
            var generation = _events.DrawGeneration;
            view.Redraw(request.ClearCache);
            return generation;
        }).ConfigureAwait(false);

        var drawCompleted = await _events.WaitForDrawAsync(
                request.ExpectedMapUri,
                afterDrawGeneration,
                request.WaitMilliseconds,
                cancellationToken)
            .ConfigureAwait(false);
        RequireSameMap(request.ExpectedMapUri);
        return new ViewMutationSnapshot(
            true,
            drawCompleted,
            await ProContext.CaptureAsync(_events).ConfigureAwait(false));
    }

    internal async Task<ViewMutationSnapshot> SetActiveTimeAsync(SetActiveTimeRequest request)
    {
        ValidateViewRequest(request);
        var start = ParseTime(request.Start, "start");
        var end = ParseTime(request.End, "end");
        if (start.HasValue && end.HasValue && start > end)
        {
            throw new ApiException(400, "invalid_time_range", "start must not be later than end.");
        }

        await QueuedTask.Run(() =>
        {
            var view = RequireActiveMap(request);
            if (view.Time is null && (start.HasValue || end.HasValue))
            {
                throw new ApiException(409, "time_not_supported", "The active map has no time-aware members.");
            }

            RequireGeneration(request.ExpectedContextGeneration!.Value);
            view.Time = new TimeRange
            {
                Start = start?.UtcDateTime,
                End = end?.UtcDateTime,
            };
        }).ConfigureAwait(false);

        RequireSameMap(request.ExpectedMapUri);
        return new ViewMutationSnapshot(
            true,
            null,
            await ProContext.CaptureAsync(_events).ConfigureAwait(false));
    }

    internal async Task<ViewMutationSnapshot> OpenStandaloneTableAsync(OpenStandaloneTableRequest request)
    {
        ValidateViewRequest(request);
        if (string.IsNullOrWhiteSpace(request.TableUri) || request.TableUri.Length > 4096)
        {
            throw new ApiException(400, "table_uri_required", "tableUri is required and must be bounded.");
        }

        var table = await QueuedTask.Run(() =>
        {
            var view = RequireActiveMap(request);
            var matches = view.Map.GetStandaloneTablesAsFlattenedList()
                .Where(item => string.Equals(item.URI, request.TableUri, StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
            {
                throw new ApiException(
                    matches.Length == 0 ? 404 : 409,
                    matches.Length == 0 ? "table_not_found" : "table_uri_ambiguous",
                    "tableUri must identify exactly one standalone table in the active map.");
            }

            RequireGeneration(request.ExpectedContextGeneration!.Value);
            return matches[0];
        }).ConfigureAwait(false);

        var opened = await FrameworkApplication.Current.Dispatcher.InvokeAsync(
            () =>
            {
                RequireGeneration(request.ExpectedContextGeneration!.Value);
                RequireSameMap(request.ExpectedMapUri);
                FrameworkExtender.OpenTablePane(
                FrameworkApplication.Panes,
                table,
                TableViewMode.eAllRecords);
                return FrameworkApplication.Panes.ActivePane is ITablePane pane &&
                       string.Equals(pane.MapMember?.URI, request.TableUri, StringComparison.Ordinal);
            });
        return new ViewMutationSnapshot(
            opened,
            null,
            await ProContext.CaptureAsync(_events).ConfigureAwait(false));
    }

    private MapView RequireActiveMap(ViewMutationRequest request)
    {
        RequireGeneration(request.ExpectedContextGeneration!.Value);
        var view = LayoutView.Active?.ActivatedMapView ?? MapView.Active;
        if (view?.Map is null)
        {
            throw new ApiException(409, "no_active_map", "An active map view is required.");
        }

        if (!string.Equals(view.Map.URI, request.ExpectedMapUri, StringComparison.Ordinal))
        {
            throw new ApiException(409, "map_changed", "The active map does not match expectedMapUri.");
        }

        return view;
    }

    private void ValidateViewRequest(ViewMutationRequest request)
    {
        if (!request.Confirm)
        {
            throw new ApiException(400, "confirmation_required", "confirm must be true.");
        }

        if (string.IsNullOrWhiteSpace(request.ExpectedMapUri) || request.ExpectedMapUri.Length > 4096)
        {
            throw new ApiException(400, "expected_map_required", "expectedMapUri is required and must be bounded.");
        }

        if (request.ExpectedContextGeneration is null or < 0)
        {
            throw new ApiException(
                400,
                "expected_context_generation_required",
                "expectedContextGeneration from the latest context snapshot is required.");
        }

        RequireGeneration(request.ExpectedContextGeneration.Value);
    }

    private void RequireGeneration(long expected)
    {
        if (_events.ContextGeneration != expected)
        {
            throw new ApiException(409, "context_changed", "The live window context changed; refresh context and retry.");
        }
    }

    private static void ValidateFinite(double? value, string name)
    {
        if (value.HasValue && !double.IsFinite(value.Value))
        {
            throw new ApiException(400, "invalid_camera_value", $"{name} must be finite.");
        }
    }

    private static TimeSpan ValidateDuration(int milliseconds)
    {
        if (milliseconds is < 0 or > 30_000)
        {
            throw new ApiException(400, "invalid_duration", "durationMilliseconds must be between 0 and 30000.");
        }

        return TimeSpan.FromMilliseconds(milliseconds);
    }

    private static double NormalizeDegrees(double value)
    {
        var normalized = value % 360;
        return normalized < 0 ? normalized + 360 : normalized;
    }

    private static double NormalizeSignedDegrees(double value)
    {
        var normalized = NormalizeDegrees(value);
        return normalized > 180 ? normalized - 360 : normalized;
    }

    private static DateTimeOffset? ParseTime(string? value, string name)
    {
        if (value is null)
        {
            return null;
        }

        if (value.Length is 0 or > 64)
        {
            throw new ApiException(400, "invalid_time", $"{name} must be a bounded ISO-8601 timestamp.");
        }

        var timeSeparator = value.IndexOf('T');
        var hasExplicitOffset = value.EndsWith("Z", StringComparison.OrdinalIgnoreCase) ||
                                (timeSeparator >= 0 &&
                                 (value.LastIndexOf('+') > timeSeparator || value.LastIndexOf('-') > timeSeparator));
        if (!hasExplicitOffset || !DateTimeOffset.TryParse(
                value,
                CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind,
                out var parsed))
        {
            throw new ApiException(
                400,
                "invalid_time",
                $"{name} must be an ISO-8601 timestamp with Z or an explicit UTC offset.");
        }

        return parsed;
    }

    private static void RequireSameMap(string expectedMapUri)
    {
        var activeMapView = LayoutView.Active?.ActivatedMapView ?? MapView.Active;
        if (!string.Equals(activeMapView?.Map.URI, expectedMapUri, StringComparison.Ordinal))
        {
            throw new ApiException(
                409,
                "mutation_context_changed",
                "The command may have completed, but the active map changed; refresh context before retrying.");
        }
    }
}
