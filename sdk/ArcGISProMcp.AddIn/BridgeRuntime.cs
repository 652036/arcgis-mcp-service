using ArcGISProMcp.AddIn.Bridge;
using ArcGISProMcp.AddIn.Editing;
using ArcGISProMcp.AddIn.Events;
using ArcGISProMcp.AddIn.Jobs;
using ArcGISProMcp.AddIn.Window;

namespace ArcGISProMcp.AddIn;

internal sealed class BridgeRuntime : IAsyncDisposable
{
    private readonly BridgeOptions _options = BridgeOptions.FromEnvironment();
    private readonly string _serverSessionId = SecurityTokens.Create();
    private readonly ProEventController _events;
    private readonly ProjectLeaseManager _leases;
    private readonly GpJobController _jobs;
    private readonly EditCommandService _edits;
    private readonly FeatureEditService _featureEdits;
    private readonly LiveWindowService _window;
    private AuthenticatedLoopbackBridge? _bridge;
    private int _disposeState;

    internal BridgeRuntime()
    {
        _events = new ProEventController(_options.EventCapacity);
        _leases = new ProjectLeaseManager(_serverSessionId, _options.LeaseTtl, _options.ProjectRoots);
        _jobs = new GpJobController(_options, _events);
        _edits = new EditCommandService(() => _events.EditGeneration);
        _featureEdits = new FeatureEditService(_events);
        _window = new LiveWindowService(_events);
        _events.ProjectChanged += OnProjectChanged;
    }

    internal string? StartupError { get; private set; }

    internal void Start()
    {
        _events.Start();
        _bridge = new AuthenticatedLoopbackBridge(
            _options,
            _serverSessionId,
            _leases,
            _events,
            _jobs,
            _edits,
            _featureEdits,
            _window);

        _ = StartCoreAsync();
    }

    private async Task StartCoreAsync()
    {
        try
        {
            await _bridge!.StartAsync().ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            // Never log request data, bearer tokens, or discovery-file contents.
            StartupError = ex.GetType().Name;
            await DisposeAsync().ConfigureAwait(false);
        }
    }

    private void OnProjectChanged()
    {
        _leases.Invalidate();
        _jobs.CancelAll();
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref _disposeState, 1) != 0)
        {
            return;
        }

        if (_bridge is not null)
        {
            await _bridge.DisposeAsync().ConfigureAwait(false);
            _bridge = null;
        }

        _events.ProjectChanged -= OnProjectChanged;
        _events.Dispose();
        await _jobs.DisposeAsync().ConfigureAwait(false);
    }
}
