using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using ArcGISProMcp.AddIn.Bridge;

namespace ArcGISProMcp.AddIn.Editing;

internal sealed class EditCommandService
{
    private readonly Func<long> _getEditGeneration;

    internal EditCommandService(Func<long> getEditGeneration)
    {
        _getEditGeneration = getEditGeneration;
    }

    internal async Task<EditStatusSnapshot> GetStatusAsync()
    {
        for (var attempt = 0; attempt < 2; attempt++)
        {
            var before = _getEditGeneration();
            var captured = await QueuedTask.Run(() =>
            {
                var project = Project.Current;
                var map = ActiveMapView()?.Map;
                var operations = map?.OperationManager;
                return new EditStatusSnapshot(
                    project is not null,
                    project?.ReadOnly ?? true,
                    project?.IsEditingEnabled ?? false,
                    project?.HasEdits ?? false,
                    map is not null,
                    map?.URI,
                    before,
                    operations?.CanUndo ?? false,
                    operations?.CanRedo ?? false);
            }).ConfigureAwait(false);

            if (before == _getEditGeneration())
            {
                return captured;
            }
        }

        throw new ApiException(409, "edit_context_busy", "The active edit context changed; retry status.");
    }

    internal async Task<EditStatusSnapshot> UndoAsync(ConfirmedCommandRequest request)
    {
        var expectedGeneration = RequireExpectedGeneration(request);
        var manager = await GetOperationManagerAsync(request, expectedGeneration).ConfigureAwait(false);
        if (!manager.CanUndo)
        {
            throw new ApiException(409, "nothing_to_undo", "The active map has no operation to undo.");
        }

        RequireGeneration(expectedGeneration);
        await manager.UndoAsync().ConfigureAwait(false);
        return await GetStatusAsync().ConfigureAwait(false);
    }

    internal async Task<EditStatusSnapshot> RedoAsync(ConfirmedCommandRequest request)
    {
        var expectedGeneration = RequireExpectedGeneration(request);
        var manager = await GetOperationManagerAsync(request, expectedGeneration).ConfigureAwait(false);
        if (!manager.CanRedo)
        {
            throw new ApiException(409, "nothing_to_redo", "The active map has no operation to redo.");
        }

        RequireGeneration(expectedGeneration);
        await manager.RedoAsync().ConfigureAwait(false);
        return await GetStatusAsync().ConfigureAwait(false);
    }

    internal async Task<EditStatusSnapshot> SaveAsync(ConfirmedCommandRequest request)
    {
        var expectedGeneration = RequireExpectedGeneration(request);
        RequireGeneration(expectedGeneration);
        var project = Project.Current ??
                      throw new ApiException(409, "no_project", "No ArcGIS Pro project is open.");

        if (project.HasEdits && !await project.SaveEditsAsync().ConfigureAwait(false))
        {
            throw new ApiException(409, "save_edits_failed", "ArcGIS Pro did not save the pending edits.");
        }

        return await GetStatusAsync().ConfigureAwait(false);
    }

    internal async Task<EditStatusSnapshot> DiscardAsync(ConfirmedCommandRequest request)
    {
        if (!request.ConfirmDiscardAll)
        {
            throw new ApiException(
                400,
                "discard_all_confirmation_required",
                "confirmDiscardAll must be true because this discards all pending project data edits.");
        }

        var expectedGeneration = RequireExpectedGeneration(request);
        RequireGeneration(expectedGeneration);
        var project = Project.Current ??
                      throw new ApiException(409, "no_project", "No ArcGIS Pro project is open.");

        if (project.HasEdits && !await project.DiscardEditsAsync().ConfigureAwait(false))
        {
            throw new ApiException(409, "discard_edits_failed", "ArcGIS Pro did not discard the pending edits.");
        }

        return await GetStatusAsync().ConfigureAwait(false);
    }

    private async Task<OperationManager> GetOperationManagerAsync(
        ConfirmedCommandRequest request,
        long expectedGeneration)
    {
        if (string.IsNullOrWhiteSpace(request.ExpectedMapUri))
        {
            throw new ApiException(400, "expected_map_required", "expectedMapUri is required for undo or redo.");
        }

        var expectedMapUri = request.ExpectedMapUri;
        var result = await QueuedTask.Run(() =>
        {
            var map = ActiveMapView()?.Map;
            if (map is null)
            {
                throw new ApiException(409, "no_active_map", "An active map view is required for undo or redo.");
            }

            if (!string.Equals(map.URI, expectedMapUri, StringComparison.Ordinal))
            {
                throw new ApiException(409, "map_changed", "The active map does not match expectedMapUri.");
            }

            return map.OperationManager;
        }).ConfigureAwait(false);

        RequireGeneration(expectedGeneration);
        return result;
    }

    private long RequireExpectedGeneration(ConfirmedCommandRequest request)
    {
        if (request.ExpectedEditGeneration is null || request.ExpectedEditGeneration < 0)
        {
            throw new ApiException(
                400,
                "expected_edit_generation_required",
                "expectedEditGeneration from the latest edit status is required.");
        }

        RequireGeneration(request.ExpectedEditGeneration.Value);
        return request.ExpectedEditGeneration.Value;
    }

    private void RequireGeneration(long expectedGeneration)
    {
        if (_getEditGeneration() != expectedGeneration)
        {
            throw new ApiException(409, "edit_context_changed", "The edit context changed; refresh status and retry.");
        }
    }

    private static MapView? ActiveMapView() => LayoutView.Active?.ActivatedMapView ?? MapView.Active;
}
