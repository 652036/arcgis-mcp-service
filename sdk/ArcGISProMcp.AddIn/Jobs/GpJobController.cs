using System.Collections.Concurrent;
using System.Globalization;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGISProMcp.AddIn.Bridge;
using ArcGISProMcp.AddIn.Events;

namespace ArcGISProMcp.AddIn.Jobs;

internal sealed class GpJobController : IAsyncDisposable
{
    private const int MaximumRetainedJobs = 64;
    private static readonly IReadOnlyDictionary<string, GpToolContract> ToolContracts =
        new Dictionary<string, GpToolContract>(StringComparer.OrdinalIgnoreCase)
        {
            ["management.CopyFeatures"] = new(
                "management.CopyFeatures", 2, 6, new[] { 0 }, new[] { 1 },
                AllowMcpAnalysisInput: true),
            ["analysis.Buffer"] = new("analysis.Buffer", 3, 11, new[] { 0 }, new[] { 1 }),
            ["analysis.PairwiseBuffer"] = new("analysis.PairwiseBuffer", 3, 11, new[] { 0 }, new[] { 1 }),

            // Spatial Analyst hydrology. These are GP toolbox tools, not arbitrary ArcPy evaluation.
            ["sa.Fill"] = new("sa.Fill", 2, 3, new[] { 0 }, new[] { 1 }, Validate: ValidateFill),
            ["sa.FlowDirection"] = new(
                "sa.FlowDirection", 2, 5, new[] { 0 }, new[] { 1 },
                OptionalOutputPathIndices: new[] { 3 }, Validate: ValidateFlowDirection),
            ["sa.FlowAccumulation"] = new(
                "sa.FlowAccumulation", 2, 5, new[] { 0 }, new[] { 1 },
                OptionalInputPathIndices: new[] { 2 }, Validate: ValidateFlowAccumulation),
            ["sa.SnapPourPoint"] = new(
                "sa.SnapPourPoint", 4, 5, new[] { 0, 1 }, new[] { 2 }, Validate: ValidateSnapPourPoint),
            ["sa.Watershed"] = new(
                "sa.Watershed", 3, 4, new[] { 0, 1 }, new[] { 2 }, Validate: ValidateWatershed),

            // Spatial Statistics core mirrored by arcgis_pro_mcp.gp_stats.
            ["stats.HotSpots"] = new(
                "stats.HotSpots", 3, 10, new[] { 0 }, new[] { 2 },
                OptionalInputPathIndices: new[] { 8 }, Validate: ValidateHotSpots),
            ["stats.OptimizedHotSpotAnalysis"] = new(
                "stats.OptimizedHotSpotAnalysis", 2, 9, new[] { 0 }, new[] { 1 },
                OptionalInputPathIndices: new[] { 4, 5 }, Validate: ValidateOptimizedHotSpots),
            ["stats.ClustersOutliers"] = new(
                "stats.ClustersOutliers", 3, 10, new[] { 0 }, new[] { 2 },
                OptionalInputPathIndices: new[] { 7 }, Validate: ValidateClustersOutliers),
            ["stats.SpatialAutocorrelation"] = new(
                "stats.SpatialAutocorrelation", 2, 7, new[] { 0 }, Array.Empty<int>(),
                Validate: ValidateSpatialAutocorrelation),
            ["stats.AverageNearestNeighbor"] = new(
                "stats.AverageNearestNeighbor", 1, 4, new[] { 0 }, Array.Empty<int>(),
                Validate: ValidateAverageNearestNeighbor),
            ["stats.MultiDistanceSpatialClustering"] = new(
                "stats.MultiDistanceSpatialClustering", 3, 8, new[] { 0 }, new[] { 1 },
                Validate: ValidateMultiDistanceClustering),

            // Network Analyst is deliberately a small, stateful GP pipeline. Analysis-layer names
            // must start with MCP_; AddLocations/Solve cannot target unrelated user layers.
            ["na.MakeRouteAnalysisLayer"] = new(
                "na.MakeRouteAnalysisLayer", 2, 3, new[] { 0 }, Array.Empty<int>(),
                Validate: ValidateMakeRoute, RequiresFileGeodatabaseWorkspace: true,
                AddOutputsToMap: true, NetworkAnalysisNameIndex: 1),
            ["na.MakeServiceAreaAnalysisLayer"] = new(
                "na.MakeServiceAreaAnalysisLayer", 2, 5, new[] { 0 }, Array.Empty<int>(),
                Validate: ValidateMakeServiceArea, RequiresFileGeodatabaseWorkspace: true,
                AddOutputsToMap: true, NetworkAnalysisNameIndex: 1),
            ["na.MakeODCostMatrixAnalysisLayer"] = new(
                "na.MakeODCostMatrixAnalysisLayer", 2, 5, new[] { 0 }, Array.Empty<int>(),
                Validate: ValidateMakeOd, RequiresFileGeodatabaseWorkspace: true,
                AddOutputsToMap: true, NetworkAnalysisNameIndex: 1),
            ["na.MakeClosestFacilityAnalysisLayer"] = new(
                "na.MakeClosestFacilityAnalysisLayer", 2, 5, new[] { 0 }, Array.Empty<int>(),
                Validate: ValidateMakeClosestFacility, RequiresFileGeodatabaseWorkspace: true,
                AddOutputsToMap: true, NetworkAnalysisNameIndex: 1),
            ["na.AddLocations"] = new(
                "na.AddLocations", 3, 3, new[] { 2 }, Array.Empty<int>(), Validate: ValidateAddLocations,
                RegisteredNetworkAnalysisIndex: 0),
            ["na.Solve"] = new(
                "na.Solve", 1, 2, Array.Empty<int>(), Array.Empty<int>(), Validate: ValidateSolve,
                RegisteredNetworkAnalysisIndex: 0),
        };
    private readonly BridgeOptions _options;
    private readonly ProEventController _events;
    private readonly ConcurrentDictionary<string, JobState> _jobs = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, NetworkAnalysisLeaseBinding> _networkAnalyses =
        new(StringComparer.OrdinalIgnoreCase);
    private readonly CancellationTokenSource _shutdown = new();

    internal GpJobController(BridgeOptions options, ProEventController events)
    {
        _options = options;
        _events = events;
    }

    internal bool HasRunningJob => _jobs.Values.Any(job => !job.IsTerminal);
    internal static IReadOnlyList<string> ContractNames => ToolContracts.Keys.OrderBy(value => value).ToArray();

    internal GpJobSnapshot Start(
        StartGpJobRequest request,
        LeaseSnapshot lease,
        Func<LeaseSnapshot, bool> leaseIsCurrent)
    {
        if (!_options.AllowWrite)
        {
            throw new ApiException(403, "write_disabled", "ARCGIS_PRO_MCP_ALLOW_WRITE is not enabled.");
        }

        if (!request.Confirm)
        {
            throw new ApiException(400, "confirmation_required", "confirm must be true.");
        }

        var normalized = ValidateAndNormalizeRequest(request, lease);
        if (HasRunningJob)
        {
            throw new ApiException(409, "job_running", "Only one SDK geoprocessing job may run at a time.");
        }

        TrimCompletedJobs();
        var jobId = SecurityTokens.Create(18);
        var state = new JobState(jobId, normalized.ToolName);
        if (!_jobs.TryAdd(jobId, state))
        {
            throw new ApiException(500, "job_id_collision", "Could not allocate a job identifier.");
        }

        if (!leaseIsCurrent(lease))
        {
            state.SetCanceledBeforeStart();
            _events.Publish("gp_job_context_changed_before_start");
            return state.Snapshot();
        }

        state.Task = RunAsync(
            state,
            normalized.Parameters,
            normalized.Environments,
            normalized.AddOutputsToMap,
            normalized.NetworkAnalysisNameToRegister,
            lease,
            () => leaseIsCurrent(lease));
        return state.Snapshot();
    }

    internal GpJobSnapshot Get(string jobId)
    {
        if (!_jobs.TryGetValue(jobId, out var state))
        {
            throw new ApiException(404, "job_not_found", "The geoprocessing job was not found.");
        }

        return state.Snapshot();
    }

    internal GpJobSnapshot Cancel(string jobId)
    {
        if (!_jobs.TryGetValue(jobId, out var state))
        {
            throw new ApiException(404, "job_not_found", "The geoprocessing job was not found.");
        }

        if (!state.RequestCancel())
        {
            throw new ApiException(409, "job_finished", "The geoprocessing job already finished.");
        }

        _events.Publish("gp_job_cancel_requested");
        return state.Snapshot();
    }

    internal void CancelAll()
    {
        _networkAnalyses.Clear();
        foreach (var state in _jobs.Values.Where(job => !job.IsTerminal))
        {
            state.RequestCancel();
        }
    }

    private async Task RunAsync(
        JobState state,
        IReadOnlyList<string> parameters,
        IReadOnlyList<KeyValuePair<string, string>> environments,
        bool addOutputsToMap,
        string? networkAnalysisNameToRegister,
        LeaseSnapshot lease,
        Func<bool> leaseIsCurrent)
    {
        state.SetRunning();
        _events.Publish("gp_job_started");
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(
            state.Cancellation.Token,
            _shutdown.Token);

        try
        {
            var values = Geoprocessing.MakeValueArray(parameters.Cast<object>().ToArray());
            var flags = GPExecuteToolFlags.GPThread;
            if (addOutputsToMap)
            {
                flags |= GPExecuteToolFlags.AddOutputsToMap;
            }

            var result = await Geoprocessing.ExecuteToolAsync(
                    state.ToolName,
                    values,
                    environments,
                    linked.Token,
                    (eventName, payload) => OnGpEvent(state, eventName, payload),
                    flags)
                .ConfigureAwait(false);

            if (result.IsCanceled)
            {
                state.SetCanceled(result.ErrorCode, !leaseIsCurrent());
                _events.Publish("gp_job_canceled");
            }
            else if (result.IsFailed)
            {
                state.SetFailed(result.ErrorCode, "Geoprocessing tool failed.", !leaseIsCurrent());
                _events.Publish("gp_job_failed");
            }
            else
            {
                var leaseCurrent = leaseIsCurrent();
                if (networkAnalysisNameToRegister is not null &&
                    leaseCurrent && !linked.IsCancellationRequested)
                {
                    _networkAnalyses[networkAnalysisNameToRegister] = new NetworkAnalysisLeaseBinding(
                        lease.LeaseId,
                        lease.ProjectUri,
                        lease.Generation);
                }

                state.SetSucceeded(
                    result.ErrorCode,
                    SecretRedactor.Sanitize(result.ReturnValue),
                    leaseCurrent);
                _events.Publish("gp_job_completed");
            }
        }
        catch (OperationCanceledException)
        {
            state.SetCanceled(null, !leaseIsCurrent());
            _events.Publish("gp_job_canceled");
        }
        catch (Exception ex)
        {
            // Exception messages can embed arguments. Only retain the exception type.
            state.SetFailed(null, ex.GetType().Name, !leaseIsCurrent());
            _events.Publish("gp_job_failed");
        }
    }

    private static void OnGpEvent(JobState state, string eventName, object payload)
    {
        if (string.Equals(eventName, "OnProgressPos", StringComparison.Ordinal) && payload is int progress)
        {
            state.SetProgress(Math.Clamp(progress, 0, 100));
        }
    }

    private NormalizedGpRequest ValidateAndNormalizeRequest(StartGpJobRequest request, LeaseSnapshot lease)
    {
        if (string.IsNullOrWhiteSpace(request.ToolName) || request.ToolName.Length > 256)
        {
            throw new ApiException(400, "invalid_tool", "toolName is required and must be at most 256 characters.");
        }

        if (!_options.GpToolAllowlist.Contains(request.ToolName))
        {
            throw new ApiException(403, "tool_not_allowlisted", "The exact geoprocessing tool is not allowlisted.");
        }

        if (!ToolContracts.TryGetValue(request.ToolName, out var contract))
        {
            throw new ApiException(
                403,
                "tool_contract_missing",
                "The tool has no built-in typed SDK bridge contract.");
        }

        if (request.Parameters is null ||
            request.Parameters.Count < contract.MinimumParameters ||
            request.Parameters.Count > contract.MaximumParameters ||
            request.Parameters.Any(value => value is null || value.Length > 32_768))
        {
            throw new ApiException(
                400,
                "invalid_parameters",
                "The parameter count or a bounded string parameter does not match the typed tool contract.");
        }

        var parameters = request.Parameters.ToArray();
        foreach (var index in contract.InputPathIndices)
        {
            if (contract.AllowMcpAnalysisInput &&
                TryNormalizeMcpAnalysisReference(parameters[index], out var analysisReference, out var analysisName))
            {
                RequireRegisteredNetworkAnalysis(analysisName, lease);
                parameters[index] = analysisReference;
            }
            else
            {
                parameters[index] = PathPolicy.ValidateInput(
                    parameters[index],
                    _options.InputRoots,
                    $"parameters[{index}]");
            }
        }

        foreach (var index in contract.OutputPathIndices)
        {
            parameters[index] = PathPolicy.ValidateOutput(
                parameters[index],
                _options.GpOutputRoot,
                $"parameters[{index}]");
        }

        foreach (var index in contract.OptionalInputPathIndices ?? Array.Empty<int>())
        {
            if (index < parameters.Length)
            {
                parameters[index] = IsMissing(parameters[index])
                    ? "#"
                    : PathPolicy.ValidateInput(parameters[index], _options.InputRoots, $"parameters[{index}]");
            }
        }

        foreach (var index in contract.OptionalOutputPathIndices ?? Array.Empty<int>())
        {
            if (index < parameters.Length)
            {
                parameters[index] = IsMissing(parameters[index])
                    ? "#"
                    : PathPolicy.ValidateOutput(parameters[index], _options.GpOutputRoot, $"parameters[{index}]");
            }
        }

        contract.Validate?.Invoke(parameters);

        if (contract.RegisteredNetworkAnalysisIndex is int registeredIndex)
        {
            RequireRegisteredNetworkAnalysis(parameters[registeredIndex], lease);
        }

        string? networkAnalysisNameToRegister = null;
        if (contract.NetworkAnalysisNameIndex is int nameIndex)
        {
            networkAnalysisNameToRegister = parameters[nameIndex];
            if (_networkAnalyses.ContainsKey(networkAnalysisNameToRegister))
            {
                throw new ApiException(
                    409,
                    "network_analysis_name_in_use",
                    "The MCP network analysis name is already registered for this bridge session.");
            }
        }

        if (request.Environments is not null && request.Environments.Count > 8)
        {
            throw new ApiException(400, "invalid_environments", "At most eight environments are allowed.");
        }

        var environments = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pair in request.Environments ?? new Dictionary<string, string>())
        {
            if (pair.Value is null || pair.Value.Length > 32_768)
            {
                throw new ApiException(400, "invalid_environment_value", "Environment values must be bounded strings.");
            }

            if (!_options.GpEnvironmentAllowlist.Contains(pair.Key))
            {
                throw new ApiException(403, "environment_not_allowlisted", "A geoprocessing environment is not allowlisted.");
            }

            if (pair.Key is not ("workspace" or "scratchWorkspace"))
            {
                throw new ApiException(
                    403,
                    "environment_contract_missing",
                    "The environment has no built-in typed SDK bridge contract.");
            }

            environments[pair.Key] = PathPolicy.ValidateOutput(
                pair.Value,
                _options.GpOutputRoot,
                $"environments.{pair.Key}");
        }

        // Never inherit a permissive global overwrite setting from the Pro session.
        environments["overwriteoutput"] = "false";
        if (contract.RequiresFileGeodatabaseWorkspace)
        {
            if (!environments.TryGetValue("workspace", out var workspace))
            {
                throw new ApiException(
                    400,
                    "network_workspace_required",
                    "Network analysis layer creation requires an explicit workspace environment.");
            }

            if (!workspace.EndsWith(".gdb", StringComparison.OrdinalIgnoreCase) || !Directory.Exists(workspace))
            {
                throw new ApiException(
                    400,
                    "file_geodatabase_workspace_required",
                    "The network workspace must be an existing file geodatabase under the GP output root.");
            }
        }

        return new NormalizedGpRequest(
            contract.CanonicalName,
            parameters,
            environments.ToArray(),
            contract.AddOutputsToMap,
            networkAnalysisNameToRegister);
    }

    private static void ValidateFill(string[] parameters) =>
        ValidateOptionalPositive(parameters, 2, "z_limit");

    private static void ValidateFlowDirection(string[] parameters)
    {
        ValidateEnum(parameters, 2, "force_flow", new[] { "NORMAL", "FORCE" }, "NORMAL");
        ValidateEnum(parameters, 4, "flow_direction_type", new[] { "D8", "MFD", "DINF" }, "D8");
    }

    private static void ValidateFlowAccumulation(string[] parameters)
    {
        ValidateEnum(parameters, 3, "data_type", new[] { "FLOAT", "INTEGER", "DOUBLE" }, "FLOAT");
        ValidateEnum(parameters, 4, "flow_direction_type", new[] { "D8", "MFD", "DINF" }, "D8");
    }

    private static void ValidateSnapPourPoint(string[] parameters)
    {
        ValidateRequiredPositive(parameters, 3, "snap_distance");
        ValidateField(parameters, 4, "pour_point_field", optional: true);
    }

    private static void ValidateWatershed(string[] parameters) =>
        ValidateField(parameters, 3, "pour_point_field", optional: true);

    private static void ValidateHotSpots(string[] parameters)
    {
        ValidateField(parameters, 1, "input_field", optional: false);
        ValidateConceptualization(parameters, 3, "FIXED_DISTANCE_BAND");
        ValidateDistanceMethod(parameters, 4);
        ValidateStandardization(parameters, 5, "NONE");
        ValidateOptionalPositive(parameters, 6, "distance_band");
        ValidateField(parameters, 7, "self_potential_field", optional: true);
        ValidateEnum(parameters, 9, "apply_fdr", new[] { "APPLY_FDR", "NO_FDR" }, "NO_FDR");
    }

    private static void ValidateOptimizedHotSpots(string[] parameters)
    {
        ValidateField(parameters, 2, "analysis_field", optional: true);
        ValidateEnum(
            parameters,
            3,
            "aggregation_method",
            new[]
            {
                "COUNT_INCIDENTS_WITHIN_FISHNET_POLYGONS",
                "COUNT_INCIDENTS_WITHIN_HEXAGON_POLYGONS",
                "COUNT_INCIDENTS_WITHIN_AGGREGATION_POLYGONS",
                "SNAP_NEARBY_INCIDENTS_TO_CREATE_WEIGHTED_POINTS",
            },
            string.Empty,
            allowMissing: true);
        ValidateField(parameters, 6, "analysis_field_on_polygons", optional: true);
        ValidateOptionalPositive(parameters, 7, "cell_size");
        ValidateOptionalPositive(parameters, 8, "distance_band");
    }

    private static void ValidateClustersOutliers(string[] parameters)
    {
        ValidateField(parameters, 1, "input_field", optional: false);
        ValidateConceptualization(parameters, 3, "FIXED_DISTANCE_BAND");
        ValidateDistanceMethod(parameters, 4);
        ValidateStandardization(parameters, 5, "NONE");
        ValidateOptionalPositive(parameters, 6, "distance_band");
        ValidateEnum(parameters, 8, "apply_fdr", new[] { "APPLY_FDR", "NO_FDR" }, "NO_FDR");
        if (!IsMissingAt(parameters, 9))
        {
            ValidateInteger(parameters[9], "number_of_permutations", 0, 999_999);
        }
    }

    private static void ValidateSpatialAutocorrelation(string[] parameters)
    {
        ValidateField(parameters, 1, "input_field", optional: false);
        ValidateEnum(parameters, 2, "generate_report", new[] { "NO_REPORT" }, "NO_REPORT");
        ValidateConceptualization(parameters, 3, "INVERSE_DISTANCE");
        ValidateDistanceMethod(parameters, 4);
        ValidateStandardization(parameters, 5, "ROW");
        ValidateOptionalPositive(parameters, 6, "distance_band");
    }

    private static void ValidateAverageNearestNeighbor(string[] parameters)
    {
        ValidateDistanceMethod(parameters, 1);
        ValidateEnum(parameters, 2, "generate_report", new[] { "NO_REPORT" }, "NO_REPORT");
        ValidateOptionalPositive(parameters, 3, "area");
    }

    private static void ValidateMultiDistanceClustering(string[] parameters)
    {
        ValidateInteger(Parameter(parameters, 2), "number_of_distance_bands", 1, 100);
        ValidateEnum(
            parameters,
            3,
            "compute_confidence_envelope",
            new[]
            {
                "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE",
                "9_PERMUTATIONS",
                "99_PERMUTATIONS",
                "999_PERMUTATIONS",
            },
            "0_PERMUTATIONS_-_NO_CONFIDENCE_ENVELOPE");
        ValidateEnum(parameters, 4, "display_results", new[] { "NO_DISPLAY" }, "NO_DISPLAY");
        ValidateField(parameters, 5, "weight_field", optional: true);
        ValidateOptionalPositive(parameters, 6, "beginning_distance");
        ValidateOptionalPositive(parameters, 7, "distance_increment");
    }

    private static void ValidateMakeRoute(string[] parameters)
    {
        parameters[1] = NormalizeMcpAnalysisLayerName(Parameter(parameters, 1));
        ValidateSafeName(parameters, 2, "travel_mode", optional: true);
    }

    private static void ValidateMakeServiceArea(string[] parameters)
    {
        parameters[1] = NormalizeMcpAnalysisLayerName(Parameter(parameters, 1));
        ValidateSafeName(parameters, 2, "travel_mode", optional: true);
        ValidateEnum(
            parameters,
            3,
            "travel_direction",
            new[] { "FROM_FACILITIES", "TO_FACILITIES" },
            "FROM_FACILITIES");
        ValidatePositiveList(parameters, 4, "cutoffs");
    }

    private static void ValidateMakeOd(string[] parameters)
    {
        parameters[1] = NormalizeMcpAnalysisLayerName(Parameter(parameters, 1));
        ValidateSafeName(parameters, 2, "travel_mode", optional: true);
        ValidateOptionalPositive(parameters, 3, "cutoff");
        if (!IsMissingAt(parameters, 4))
        {
            ValidateInteger(parameters[4], "number_of_destinations_to_find", 1, 10_000);
        }
    }

    private static void ValidateMakeClosestFacility(string[] parameters)
    {
        parameters[1] = NormalizeMcpAnalysisLayerName(Parameter(parameters, 1));
        ValidateSafeName(parameters, 2, "travel_mode", optional: true);
        ValidateEnum(
            parameters,
            3,
            "travel_direction",
            new[] { "TO_FACILITIES", "FROM_FACILITIES" },
            "TO_FACILITIES");
        ValidateOptionalPositive(parameters, 4, "cutoff");
    }

    private static void ValidateAddLocations(string[] parameters)
    {
        parameters[0] = NormalizeMcpAnalysisLayerName(Parameter(parameters, 0));
        ValidateEnum(
            parameters,
            1,
            "sub_layer",
            new[]
            {
                "STOPS",
                "FACILITIES",
                "INCIDENTS",
                "ORIGINS",
                "DESTINATIONS",
                "POINT BARRIERS",
                "LINE BARRIERS",
                "POLYGON BARRIERS",
            },
            string.Empty,
            allowMissing: false);
    }

    private static void ValidateSolve(string[] parameters)
    {
        parameters[0] = NormalizeMcpAnalysisLayerName(Parameter(parameters, 0));
        ValidateEnum(parameters, 1, "ignore_invalids", new[] { "SKIP", "HALT" }, "SKIP");
    }

    private static void ValidateConceptualization(string[] parameters, int index, string defaultValue) =>
        ValidateEnum(
            parameters,
            index,
            "conceptualization",
            new[]
            {
                "INVERSE_DISTANCE",
                "INVERSE_DISTANCE_SQUARED",
                "FIXED_DISTANCE_BAND",
                "ZONE_OF_INDIFFERENCE",
                "CONTIGUITY_EDGES_ONLY",
                "CONTIGUITY_EDGES_CORNERS",
                "GET_SPATIAL_WEIGHTS_FROM_FILE",
            },
            defaultValue);

    private static void ValidateDistanceMethod(string[] parameters, int index) =>
        ValidateEnum(
            parameters,
            index,
            "distance_method",
            new[] { "EUCLIDEAN_DISTANCE", "MANHATTAN_DISTANCE" },
            "EUCLIDEAN_DISTANCE");

    private static void ValidateStandardization(string[] parameters, int index, string defaultValue) =>
        ValidateEnum(parameters, index, "standardization", new[] { "NONE", "ROW" }, defaultValue);

    private static void ValidateEnum(
        string[] parameters,
        int index,
        string label,
        IReadOnlyCollection<string> allowed,
        string defaultValue,
        bool allowMissing = true)
    {
        if (index >= parameters.Length || IsMissing(parameters[index]))
        {
            if (!allowMissing)
            {
                throw new ApiException(400, "invalid_gp_parameter", $"{label} is required by the typed contract.");
            }

            if (index < parameters.Length && defaultValue.Length > 0)
            {
                parameters[index] = defaultValue;
            }

            return;
        }

        var normalized = parameters[index].Trim().ToUpperInvariant();
        if (!allowed.Contains(normalized, StringComparer.Ordinal))
        {
            throw new ApiException(400, "invalid_gp_parameter", $"{label} is outside the typed enum contract.");
        }

        parameters[index] = normalized;
    }

    private static void ValidateField(string[] parameters, int index, string label, bool optional)
    {
        if (index >= parameters.Length || IsMissing(parameters[index]))
        {
            if (!optional)
            {
                throw new ApiException(400, "invalid_gp_parameter", $"{label} is required.");
            }

            return;
        }

        var value = parameters[index].Trim();
        if (value.Length > 128 || value.Any(character => character is ';' or '\r' or '\n' or '\0'))
        {
            throw new ApiException(400, "invalid_gp_parameter", $"{label} is not a bounded field name.");
        }

        parameters[index] = value;
    }

    private static void ValidateSafeName(string[] parameters, int index, string label, bool optional)
    {
        if (index >= parameters.Length || IsMissing(parameters[index]))
        {
            if (!optional)
            {
                throw new ApiException(400, "invalid_gp_parameter", $"{label} is required.");
            }

            return;
        }

        var value = parameters[index].Trim();
        if (value.Length > 256 || value.Any(char.IsControl) || value.Contains(';'))
        {
            throw new ApiException(400, "invalid_gp_parameter", $"{label} is not a bounded safe name.");
        }

        parameters[index] = value;
    }

    private static void ValidateRequiredPositive(string[] parameters, int index, string label)
    {
        if (index >= parameters.Length || IsMissing(parameters[index]) ||
            !double.TryParse(parameters[index], NumberStyles.Float, CultureInfo.InvariantCulture, out var value) ||
            !double.IsFinite(value) || value <= 0)
        {
            throw new ApiException(400, "invalid_gp_parameter", $"{label} must be a finite number greater than zero.");
        }

        parameters[index] = value.ToString("R", CultureInfo.InvariantCulture);
    }

    private static void ValidateOptionalPositive(string[] parameters, int index, string label)
    {
        if (!IsMissingAt(parameters, index))
        {
            ValidateRequiredPositive(parameters, index, label);
        }
    }

    private static void ValidateInteger(string value, string label, int minimum, int maximum)
    {
        if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var parsed) ||
            parsed < minimum || parsed > maximum)
        {
            throw new ApiException(400, "invalid_gp_parameter", $"{label} is outside the typed integer range.");
        }
    }

    private static void ValidatePositiveList(string[] parameters, int index, string label)
    {
        if (IsMissingAt(parameters, index))
        {
            return;
        }

        var values = parameters[index].Split(
            new[] { ' ', ',', ';' },
            StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (values.Length is 0 or > 32)
        {
            throw new ApiException(400, "invalid_gp_parameter", $"{label} must contain 1 to 32 positive values.");
        }

        foreach (var value in values)
        {
            if (!double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var parsed) ||
                !double.IsFinite(parsed) || parsed <= 0)
            {
                throw new ApiException(400, "invalid_gp_parameter", $"{label} must contain only finite positive values.");
            }
        }

        parameters[index] = string.Join(" ", values);
    }

    private static string NormalizeMcpAnalysisLayerName(string value)
    {
        var trimmed = value.Trim();
        if (trimmed.Length is < 5 or > 64 || !trimmed.StartsWith("MCP_", StringComparison.Ordinal) ||
            trimmed.Any(character =>
                character != '_' &&
                character is not (>= 'A' and <= 'Z') &&
                character is not (>= 'a' and <= 'z') &&
                character is not (>= '0' and <= '9')))
        {
            throw new ApiException(
                400,
                "invalid_network_layer_name",
                "Network analysis layer names must use the MCP_ prefix and only ASCII-style letters, digits, or underscore.");
        }

        return trimmed;
    }

    private static bool TryNormalizeMcpAnalysisReference(
        string value,
        out string normalizedReference,
        out string analysisName)
    {
        normalizedReference = string.Empty;
        analysisName = string.Empty;
        var normalized = value.Replace('/', '\\');
        var parts = normalized.Split('\\', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length is < 1 or > 2)
        {
            return false;
        }

        try
        {
            analysisName = NormalizeMcpAnalysisLayerName(parts[0]);
        }
        catch (ApiException)
        {
            return false;
        }

        if (parts.Length == 2 && !new[]
        {
            "Routes", "Stops", "Directions", "Polygons", "Facilities", "Incidents", "Origins", "Destinations", "Lines",
        }.Contains(parts[1], StringComparer.OrdinalIgnoreCase))
        {
            return false;
        }

        normalizedReference = parts.Length == 1 ? analysisName : analysisName + "\\" + parts[1];
        return true;
    }

    private void RequireRegisteredNetworkAnalysis(string analysisName, LeaseSnapshot lease)
    {
        var normalized = NormalizeMcpAnalysisLayerName(analysisName);
        if (!_networkAnalyses.TryGetValue(normalized, out var binding) ||
            binding.Generation != lease.Generation ||
            !ProjectIdentity.Equals(binding.ProjectUri, lease.ProjectUri) ||
            !SecurityTokens.FixedTimeEquals(binding.LeaseId, lease.LeaseId))
        {
            throw new ApiException(
                409,
                "network_analysis_not_registered",
                "The network analysis layer was not created successfully by this bridge under the current lease.");
        }
    }

    private static string Parameter(string[] parameters, int index)
    {
        if (index >= parameters.Length || IsMissing(parameters[index]))
        {
            throw new ApiException(400, "invalid_gp_parameter", $"parameters[{index}] is required.");
        }

        return parameters[index];
    }

    private static bool IsMissingAt(string[] parameters, int index) =>
        index >= parameters.Length || IsMissing(parameters[index]);

    private static bool IsMissing(string value) =>
        string.IsNullOrWhiteSpace(value) || string.Equals(value.Trim(), "#", StringComparison.Ordinal);

    private void TrimCompletedJobs()
    {
        if (_jobs.Count < MaximumRetainedJobs)
        {
            return;
        }

        foreach (var state in _jobs.Values
                     .Where(item => item.IsTerminal)
                     .OrderBy(item => item.CreatedAtUtc)
                     .Take(_jobs.Count - MaximumRetainedJobs + 1))
        {
            if (_jobs.TryRemove(state.JobId, out var removed))
            {
                removed.Dispose();
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        _shutdown.Cancel();
        foreach (var job in _jobs.Values)
        {
            job.Cancellation.Cancel();
        }

        var tasks = _jobs.Values.Select(job => job.Task).Where(task => task is not null).Cast<Task>();
        try
        {
            await Task.WhenAll(tasks).WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is OperationCanceledException or TimeoutException)
        {
            // ArcGIS Pro is unloading; cancellation was already requested.
        }

        foreach (var job in _jobs.Values)
        {
            job.Dispose();
        }

        _shutdown.Dispose();
    }

    private sealed class JobState : IDisposable
    {
        private readonly object _gate = new();
        private GpJobStatus _status = GpJobStatus.Queued;
        private int? _progress;
        private int? _errorCode;
        private string? _returnValue;
        private string? _error;
        private DateTimeOffset? _completedAtUtc;
        private bool _cancellationRequested;
        private bool _leaseInvalidated;

        internal JobState(string jobId, string toolName)
        {
            JobId = jobId;
            ToolName = toolName;
            CreatedAtUtc = DateTimeOffset.UtcNow;
        }

        internal string JobId { get; }
        internal string ToolName { get; }
        internal DateTimeOffset CreatedAtUtc { get; }
        internal CancellationTokenSource Cancellation { get; } = new();
        internal Task? Task { get; set; }

        internal bool IsTerminal
        {
            get
            {
                lock (_gate)
                {
                    return IsTerminalStatus(_status);
                }
            }
        }

        internal void SetRunning()
        {
            lock (_gate)
            {
                if (_status == GpJobStatus.Queued)
                {
                    _status = GpJobStatus.Running;
                }
            }
        }

        internal void SetProgress(int progress)
        {
            lock (_gate)
            {
                _progress = progress;
            }
        }

        internal bool RequestCancel()
        {
            var cancel = false;
            lock (_gate)
            {
                if (IsTerminalStatus(_status))
                {
                    return false;
                }

                _status = GpJobStatus.CancelRequested;
                _cancellationRequested = true;
                cancel = true;
            }

            if (cancel)
            {
                Cancellation.Cancel();
            }

            return true;
        }

        internal void SetSucceeded(int errorCode, string? returnValue, bool leaseCurrent)
        {
            lock (_gate)
            {
                _leaseInvalidated = !leaseCurrent;
                _status = _cancellationRequested
                    ? GpJobStatus.SucceededAfterCancellationRequest
                    : leaseCurrent
                        ? GpJobStatus.Succeeded
                        : GpJobStatus.SucceededAfterLeaseInvalidation;
                _progress = 100;
                _errorCode = errorCode;
                _returnValue = returnValue;
                _completedAtUtc = DateTimeOffset.UtcNow;
            }
        }

        internal void SetFailed(int? errorCode, string error, bool leaseInvalidated)
        {
            lock (_gate)
            {
                _status = GpJobStatus.Failed;
                _errorCode = errorCode;
                _error = SecretRedactor.Sanitize(error);
                _leaseInvalidated = leaseInvalidated;
                _completedAtUtc = DateTimeOffset.UtcNow;
            }
        }

        internal void SetCanceled(int? errorCode, bool leaseInvalidated)
        {
            lock (_gate)
            {
                _status = GpJobStatus.Canceled;
                _errorCode = errorCode;
                _leaseInvalidated = leaseInvalidated;
                _completedAtUtc = DateTimeOffset.UtcNow;
            }
        }

        internal void SetCanceledBeforeStart()
        {
            lock (_gate)
            {
                _status = GpJobStatus.CanceledBeforeStart;
                _leaseInvalidated = true;
                _completedAtUtc = DateTimeOffset.UtcNow;
            }
        }

        internal GpJobSnapshot Snapshot()
        {
            lock (_gate)
            {
                return new GpJobSnapshot(
                    JobId,
                    ToolName,
                    _status,
                    _progress,
                    _errorCode,
                    _returnValue,
                    _error,
                    _cancellationRequested,
                    _leaseInvalidated,
                    CreatedAtUtc,
                    _completedAtUtc);
            }
        }

        private static bool IsTerminalStatus(GpJobStatus status) =>
            status is GpJobStatus.Succeeded or
                GpJobStatus.Failed or
                GpJobStatus.Canceled or
                GpJobStatus.CanceledBeforeStart or
                GpJobStatus.SucceededAfterCancellationRequest or
                GpJobStatus.SucceededAfterLeaseInvalidation;

        public void Dispose() => Cancellation.Dispose();
    }

    private sealed record GpToolContract(
        string CanonicalName,
        int MinimumParameters,
        int MaximumParameters,
        IReadOnlyList<int> InputPathIndices,
        IReadOnlyList<int> OutputPathIndices,
        IReadOnlyList<int>? OptionalInputPathIndices = null,
        IReadOnlyList<int>? OptionalOutputPathIndices = null,
        Action<string[]>? Validate = null,
        bool AllowMcpAnalysisInput = false,
        bool RequiresFileGeodatabaseWorkspace = false,
        bool AddOutputsToMap = false,
        int? NetworkAnalysisNameIndex = null,
        int? RegisteredNetworkAnalysisIndex = null);

    private sealed record NormalizedGpRequest(
        string ToolName,
        IReadOnlyList<string> Parameters,
        IReadOnlyList<KeyValuePair<string, string>> Environments,
        bool AddOutputsToMap,
        string? NetworkAnalysisNameToRegister);

    private sealed record NetworkAnalysisLeaseBinding(
        string LeaseId,
        string ProjectUri,
        long Generation);
}
