using ArcGIS.Desktop.Editing.Events;
using ArcGIS.Desktop.Core.Events;
using ArcGIS.Desktop.Mapping.Events;
using ArcGIS.Desktop.Framework.Events;
using ArcGIS.Desktop.Layouts.Events;
using ArcGISProMcp.AddIn.Bridge;

namespace ArcGISProMcp.AddIn.Events;

internal sealed class ProEventController : IDisposable
{
    private readonly object _gate = new();
    private readonly Queue<BridgeEvent> _events = new();
    private readonly int _capacity;
    private TaskCompletionSource<bool> _changed = NewSignal();
    private TaskCompletionSource<bool> _drawChanged = NewSignal();
    private readonly Dictionary<string, long> _lastDrawGenerationByMap = new(StringComparer.Ordinal);
    private long _sequence;
    private long _contextGeneration;
    private long _selectionGeneration;
    private long _editGeneration;
    private long _drawGeneration;
    private bool _started;

    internal ProEventController(int capacity)
    {
        _capacity = Math.Clamp(capacity, 32, 4096);
    }

    internal event Action? ProjectChanged;
    internal long ContextGeneration => Interlocked.Read(ref _contextGeneration);
    internal long SelectionGeneration => Interlocked.Read(ref _selectionGeneration);
    internal long EditGeneration => Interlocked.Read(ref _editGeneration);
    internal long DrawGeneration => Interlocked.Read(ref _drawGeneration);

    internal ContextGenerations Generations => new(
        ContextGeneration,
        SelectionGeneration,
        EditGeneration,
        DrawGeneration);

    internal void Start()
    {
        lock (_gate)
        {
            if (_started)
            {
                return;
            }

            ActiveMapViewChangedEvent.Subscribe(OnActiveMapViewChanged);
            ActivePaneChangedEvent.Subscribe(OnActivePaneChanged);
            MapViewCameraChangedEvent.Subscribe(OnMapViewCameraChanged);
            TOCSelectionChangedEvent.Subscribe(OnTocSelectionChanged);
            MapSelectionChangedEvent.Subscribe(OnMapSelectionChanged);
            DrawCompleteEvent.Subscribe(OnDrawComplete);
            MapViewTimeChangedEvent.Subscribe(OnMapViewTimeChanged);
            ElementEvent.Subscribe(OnLayoutElementEvent);
            EditCompletedEvent.Subscribe(OnEditCompletedAsync);
            ProjectOpenedEvent.Subscribe(OnProjectOpened);
            ProjectClosedEvent.Subscribe(OnProjectClosed);
            _started = true;
        }

        Publish("bridge_started");
    }

    internal void Publish(string eventType)
    {
        TaskCompletionSource<bool> signal;
        lock (_gate)
        {
            var item = new BridgeEvent(
                Interlocked.Increment(ref _sequence),
                eventType,
                DateTimeOffset.UtcNow);
            _events.Enqueue(item);
            while (_events.Count > _capacity)
            {
                _events.Dequeue();
            }

            signal = _changed;
            _changed = NewSignal();
        }

        signal.TrySetResult(true);
    }

    internal async Task<EventBatch> ReadAsync(
        long after,
        int limit,
        int waitMilliseconds,
        CancellationToken cancellationToken)
    {
        limit = Math.Clamp(limit, 1, 256);
        waitMilliseconds = Math.Clamp(waitMilliseconds, 0, 30_000);

        var batch = Snapshot(after, limit, out var signal);
        if (batch.Events.Count > 0 || waitMilliseconds == 0)
        {
            return batch;
        }

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(waitMilliseconds);
        try
        {
            await signal.WaitAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            // A long poll timeout returns an empty batch.
        }

        return Snapshot(after, limit, out _);
    }

    private EventBatch Snapshot(long after, int limit, out Task signal)
    {
        lock (_gate)
        {
            var firstSequence = _events.Count == 0 ? _sequence + 1 : _events.Peek().Sequence;
            var truncated = after > 0 && after < firstSequence - 1;
            var selected = _events.Where(item => item.Sequence > after).Take(limit).ToArray();
            var last = selected.Length == 0 ? after : selected[^1].Sequence;
            signal = _changed.Task;
            return new EventBatch(after, last, truncated, selected);
        }
    }

    internal void MarkEditContextChanged(string eventType)
    {
        Interlocked.Increment(ref _contextGeneration);
        Interlocked.Increment(ref _editGeneration);
        Publish(eventType);
    }

    private void OnActiveMapViewChanged(ActiveMapViewChangedEventArgs _) =>
        MarkEditContextChanged("active_map_view_changed");
    private void OnActivePaneChanged(PaneEventArgs _) => MarkEditContextChanged("active_pane_changed");
    private void OnMapViewCameraChanged(MapViewCameraChangedEventArgs _)
    {
        Interlocked.Increment(ref _contextGeneration);
        Publish("map_view_camera_changed");
    }

    private void OnTocSelectionChanged(MapViewEventArgs _)
    {
        Interlocked.Increment(ref _contextGeneration);
        Publish("toc_selection_changed");
    }

    private void OnMapSelectionChanged(MapSelectionChangedEventArgs _)
    {
        Interlocked.Increment(ref _contextGeneration);
        Interlocked.Increment(ref _selectionGeneration);
        Publish("map_selection_changed");
    }

    private void OnMapViewTimeChanged(MapViewTimeChangedEventArgs _)
    {
        Interlocked.Increment(ref _contextGeneration);
        Publish("map_view_time_changed");
    }

    private void OnLayoutElementEvent(ElementEventArgs args)
    {
        if (args.Hint is not (ElementEventHint.MapFrameActivated or
            ElementEventHint.MapFrameDeactivated or ElementEventHint.MapFrameNavigated))
        {
            return;
        }

        Interlocked.Increment(ref _contextGeneration);
        Publish("layout_map_frame_changed");
    }

    private void OnDrawComplete(MapViewEventArgs args)
    {
        TaskCompletionSource<bool> signal;
        lock (_gate)
        {
            var generation = Interlocked.Increment(ref _drawGeneration);
            var mapUri = args.MapView?.Map?.URI;
            if (!string.IsNullOrWhiteSpace(mapUri))
            {
                _lastDrawGenerationByMap[mapUri] = generation;
            }

            signal = _drawChanged;
            _drawChanged = NewSignal();
        }

        signal.TrySetResult(true);
        Publish("draw_complete");
    }

    internal async Task<bool> WaitForDrawAsync(
        string expectedMapUri,
        long afterGeneration,
        int waitMilliseconds,
        CancellationToken cancellationToken)
    {
        waitMilliseconds = Math.Clamp(waitMilliseconds, 1, 30_000);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(waitMilliseconds);
        while (true)
        {
            Task signal;
            lock (_gate)
            {
                if (_lastDrawGenerationByMap.TryGetValue(expectedMapUri, out var generation) &&
                    generation > afterGeneration)
                {
                    return true;
                }

                signal = _drawChanged.Task;
            }

            try
            {
                await signal.WaitAsync(timeout.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                return false;
            }
        }

    }

    private Task OnEditCompletedAsync(EditCompletedEventArgs _)
    {
        MarkEditContextChanged("edit_completed");
        return Task.CompletedTask;
    }

    private void OnProjectOpened(ProjectEventArgs _)
    {
        Interlocked.Increment(ref _contextGeneration);
        Interlocked.Increment(ref _selectionGeneration);
        Interlocked.Increment(ref _editGeneration);
        ProjectChanged?.Invoke();
        Publish("project_opened");
    }

    private void OnProjectClosed(ProjectEventArgs _)
    {
        Interlocked.Increment(ref _contextGeneration);
        Interlocked.Increment(ref _selectionGeneration);
        Interlocked.Increment(ref _editGeneration);
        ProjectChanged?.Invoke();
        Publish("project_closed");
    }

    private static TaskCompletionSource<bool> NewSignal() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    public void Dispose()
    {
        lock (_gate)
        {
            if (!_started)
            {
                return;
            }

            ActiveMapViewChangedEvent.Unsubscribe(OnActiveMapViewChanged);
            ActivePaneChangedEvent.Unsubscribe(OnActivePaneChanged);
            MapViewCameraChangedEvent.Unsubscribe(OnMapViewCameraChanged);
            TOCSelectionChangedEvent.Unsubscribe(OnTocSelectionChanged);
            MapSelectionChangedEvent.Unsubscribe(OnMapSelectionChanged);
            DrawCompleteEvent.Unsubscribe(OnDrawComplete);
            MapViewTimeChangedEvent.Unsubscribe(OnMapViewTimeChanged);
            ElementEvent.Unsubscribe(OnLayoutElementEvent);
            EditCompletedEvent.Unsubscribe(OnEditCompletedAsync);
            ProjectOpenedEvent.Unsubscribe(OnProjectOpened);
            ProjectClosedEvent.Unsubscribe(OnProjectClosed);
            _started = false;
        }

        Publish("bridge_stopped");
    }
}
