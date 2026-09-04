using System.Text.Json.Serialization;

namespace ArcGISProMcp.AddIn.Bridge;

internal static class BridgeJson
{
    internal static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web)
    {
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNameCaseInsensitive = false,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
    };

    static BridgeJson()
    {
        Options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.CamelCase));
    }
}

internal sealed class ApiException : Exception
{
    internal ApiException(int statusCode, string code, string message)
        : base(message)
    {
        StatusCode = statusCode;
        Code = code;
    }

    internal int StatusCode { get; }
    internal string Code { get; }
}

internal sealed record DiscoveryDocument(
    string ProtocolVersion,
    int ProcessId,
    int Port,
    string Token,
    string ServerSessionId,
    DateTimeOffset CreatedAtUtc);

internal sealed record ProContextSnapshot(
    string ProjectUri,
    string ProjectName,
    bool ProjectReadOnly,
    string ActiveViewType,
    string? ActiveViewUri,
    string? ActiveMapName,
    string? ActiveMapUri,
    CameraSnapshot? Camera,
    ActiveLayerSnapshot? ActiveLayer,
    int SelectedLayerCount,
    SelectionSnapshot Selection,
    TimeRangeSnapshot? ActiveTime,
    long ContextGeneration,
    long SelectionGeneration,
    long EditGeneration,
    long DrawGeneration);

internal sealed record CameraSnapshot(
    double? X,
    double? Y,
    double? Z,
    double? Scale,
    double? Heading,
    double? Pitch,
    double? Roll,
    int SpatialReferenceWkid,
    string? SpatialReferenceName);

internal sealed record ActiveLayerSnapshot(string LayerUri, string Name, string LayerType);

internal sealed record LayerSelectionSnapshot(
    string LayerUri,
    string Name,
    int Count,
    string OidDigest);

internal sealed record SelectionSnapshot(
    int TotalCount,
    string OidDigest,
    IReadOnlyList<LayerSelectionSnapshot> Layers);

internal sealed record TimeRangeSnapshot(string? Start, string? End);

internal sealed record ContextGenerations(
    long ContextGeneration,
    long SelectionGeneration,
    long EditGeneration,
    long DrawGeneration);

internal sealed class AcquireLeaseRequest
{
    public string ServerSessionId { get; init; } = string.Empty;
    public string ExpectedProjectUri { get; init; } = string.Empty;
    public int? TtlSeconds { get; init; }
}

internal sealed record LeaseSnapshot(
    string LeaseId,
    string ServerSessionId,
    string ProjectUri,
    long Generation,
    DateTimeOffset ExpiresAtUtc);

internal sealed class ConfirmedCommandRequest
{
    public bool Confirm { get; init; }
    public string? ExpectedMapUri { get; init; }
    public long? ExpectedContextGeneration { get; init; }
    public long? ExpectedEditGeneration { get; init; }
    public bool ConfirmDiscardAll { get; init; }
}

internal abstract class ViewMutationRequest
{
    public bool Confirm { get; init; }
    public string ExpectedMapUri { get; init; } = string.Empty;
    public long? ExpectedContextGeneration { get; init; }
}

internal sealed class SetCameraRequest : ViewMutationRequest
{
    public int? ExpectedSpatialReferenceWkid { get; init; }
    public double? X { get; init; }
    public double? Y { get; init; }
    public double? Z { get; init; }
    public double? Scale { get; init; }
    public double? Heading { get; init; }
    public double? Pitch { get; init; }
    public double? Roll { get; init; }
    public int DurationMilliseconds { get; init; }
}

internal sealed class ZoomLayerRequest : ViewMutationRequest
{
    public string LayerUri { get; init; } = string.Empty;
    public bool SelectedOnly { get; init; }
    public int DurationMilliseconds { get; init; }
    public bool MaintainViewDirection { get; init; } = true;
}

internal sealed class RefreshViewRequest : ViewMutationRequest
{
    public bool ClearCache { get; init; }
    public int WaitMilliseconds { get; init; } = 10_000;
}

internal sealed class SetActiveTimeRequest : ViewMutationRequest
{
    public string? Start { get; init; }
    public string? End { get; init; }
}

internal sealed class OpenStandaloneTableRequest : ViewMutationRequest
{
    public string TableUri { get; init; } = string.Empty;
}

internal sealed record ViewMutationSnapshot(
    bool Completed,
    bool? DrawCompleted,
    ProContextSnapshot Context);

internal abstract class FeatureEditRequest : ViewMutationRequest
{
    public string LayerUri { get; init; } = string.Empty;
    public long? ExpectedEditGeneration { get; init; }
}

internal sealed class CreateFeatureRequest : FeatureEditRequest
{
    public GeometryRequest? Geometry { get; init; }
    public IReadOnlyDictionary<string, JsonElement>? Attributes { get; init; }
}

internal abstract class SelectedFeatureEditRequest : FeatureEditRequest
{
    public long? ExpectedSelectionGeneration { get; init; }
    public int? ExpectedCount { get; init; }
    public string ExpectedOidDigest { get; init; } = string.Empty;
}

internal sealed class ModifyFeaturesRequest : SelectedFeatureEditRequest
{
    public GeometryRequest? Geometry { get; init; }
    public IReadOnlyDictionary<string, JsonElement>? Attributes { get; init; }
}

internal sealed class DeleteFeaturesRequest : SelectedFeatureEditRequest
{
    public bool ConfirmDeleteSelection { get; init; }
}

internal sealed class GeometryRequest
{
    public string Type { get; init; } = string.Empty;
    public int SpatialReferenceWkid { get; init; }
    public double[][]? Coordinates { get; init; }
}

internal sealed record FeatureEditSnapshot(
    string Operation,
    string LayerUri,
    int AffectedCount,
    string? OidDigest,
    ProContextSnapshot Context);

internal sealed class StartGpJobRequest
{
    public string ToolName { get; init; } = string.Empty;
    public IReadOnlyList<string>? Parameters { get; init; }
    public IReadOnlyDictionary<string, string>? Environments { get; init; }
    public bool Confirm { get; init; }
}

internal sealed record EventBatch(
    long After,
    long LastSequence,
    bool Truncated,
    IReadOnlyList<BridgeEvent> Events);

internal sealed record BridgeEvent(long Sequence, string Type, DateTimeOffset TimestampUtc);

internal enum GpJobStatus
{
    Queued,
    Running,
    Succeeded,
    Failed,
    CancelRequested,
    Canceled,
    CanceledBeforeStart,
    SucceededAfterCancellationRequest,
    SucceededAfterLeaseInvalidation,
}

internal sealed record GpJobSnapshot(
    string JobId,
    string ToolName,
    GpJobStatus Status,
    int? Progress,
    int? ErrorCode,
    string? ReturnValue,
    string? Error,
    bool CancellationRequested,
    bool LeaseInvalidated,
    DateTimeOffset CreatedAtUtc,
    DateTimeOffset? CompletedAtUtc);

internal sealed record EditStatusSnapshot(
    bool HasProject,
    bool ProjectReadOnly,
    bool EditingEnabled,
    bool HasEdits,
    bool HasActiveMap,
    string? ActiveMapUri,
    long EditGeneration,
    bool CanUndo,
    bool CanRedo);

internal sealed record HttpRequestData(
    string Method,
    string Target,
    IReadOnlyDictionary<string, string> Headers,
    byte[] Body);
