using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;

namespace ArcGISProMcp.AddIn;

internal sealed class McpBridgeModule : Module
{
    private static McpBridgeModule? _current;
    private BridgeRuntime? _runtime;

    internal static McpBridgeModule Current =>
        _current ??= (McpBridgeModule)FrameworkApplication.FindModule("ArcGISProMcp_AddIn_Module");

    protected override bool Initialize()
    {
        _runtime = new BridgeRuntime();
        _runtime.Start();
        return base.Initialize();
    }

    protected override void Uninitialize()
    {
        if (_runtime is not null)
        {
            _runtime.DisposeAsync().AsTask().GetAwaiter().GetResult();
            _runtime = null;
        }

        base.Uninitialize();
    }
}
