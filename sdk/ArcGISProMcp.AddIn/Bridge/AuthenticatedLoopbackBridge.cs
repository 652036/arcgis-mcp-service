using System.Globalization;
using System.Collections.Concurrent;
using System.Net;
using System.Net.Sockets;
using ArcGISProMcp.AddIn.Editing;
using ArcGISProMcp.AddIn.Events;
using ArcGISProMcp.AddIn.Jobs;
using ArcGISProMcp.AddIn.Window;

namespace ArcGISProMcp.AddIn.Bridge;

internal sealed class AuthenticatedLoopbackBridge : IAsyncDisposable
{
    private readonly BridgeOptions _options;
    private readonly string _token = SecurityTokens.Create();
    private readonly string _serverSessionId;
    private readonly ProjectLeaseManager _leases;
    private readonly ProEventController _events;
    private readonly GpJobController _jobs;
    private readonly EditCommandService _edits;
    private readonly FeatureEditService _featureEdits;
    private readonly LiveWindowService _window;
    private readonly CancellationTokenSource _shutdown = new();
    private readonly SemaphoreSlim _mutationGate = new(1, 1);
    private readonly SemaphoreSlim _clientSlots;
    private readonly ConcurrentDictionary<long, Task> _clients = new();
    private TcpListener? _listener;
    private Task? _acceptLoop;
    private DiscoveryFile? _discoveryFile;
    private int _port;
    private long _clientSequence;

    internal AuthenticatedLoopbackBridge(
        BridgeOptions options,
        string serverSessionId,
        ProjectLeaseManager leases,
        ProEventController events,
        GpJobController jobs,
        EditCommandService edits,
        FeatureEditService featureEdits,
        LiveWindowService window)
    {
        _options = options;
        _serverSessionId = serverSessionId;
        _leases = leases;
        _events = events;
        _jobs = jobs;
        _edits = edits;
        _featureEdits = featureEdits;
        _window = window;
        _clientSlots = new SemaphoreSlim(options.MaxConcurrentClients, options.MaxConcurrentClients);
    }

    internal async Task StartAsync()
    {
        if (_listener is not null)
        {
            return;
        }

        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start(16);
        _listener = listener;
        _port = ((IPEndPoint)listener.LocalEndpoint).Port;

        try
        {
            _discoveryFile = await DiscoveryFile.CreateAsync(
                    _options.DiscoveryDirectory,
                    new DiscoveryDocument(
                        BridgeOptions.ProtocolVersion,
                        Environment.ProcessId,
                        _port,
                        _token,
                        _serverSessionId,
                        DateTimeOffset.UtcNow),
                    _shutdown.Token)
                .ConfigureAwait(false);
            _acceptLoop = AcceptLoopAsync(_shutdown.Token);
        }
        catch
        {
            listener.Stop();
            _listener = null;
            throw;
        }
    }

    private async Task AcceptLoopAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            TcpClient client;
            try
            {
                client = await _listener!.AcceptTcpClientAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }
            catch (SocketException) when (cancellationToken.IsCancellationRequested)
            {
                return;
            }

            if (!_clientSlots.Wait(0))
            {
                client.Dispose();
                continue;
            }

            var clientId = Interlocked.Increment(ref _clientSequence);
            var task = HandleClientAsync(client, cancellationToken);
            _clients[clientId] = task;
            _ = task.ContinueWith(
                _completed =>
                {
                    _clients.TryRemove(clientId, out _);
                    _clientSlots.Release();
                },
                CancellationToken.None,
                TaskContinuationOptions.ExecuteSynchronously,
                TaskScheduler.Default);
        }
    }

    private async Task HandleClientAsync(TcpClient client, CancellationToken cancellationToken)
    {
        using (client)
        {
            if (client.Client.RemoteEndPoint is not IPEndPoint remote || !IPAddress.IsLoopback(remote.Address))
            {
                return;
            }

            client.NoDelay = true;
            var stream = client.GetStream();
            try
            {
                var request = await ReadRequestWithTimeoutAsync(stream, cancellationToken).ConfigureAwait(false);
                var response = await DispatchAsync(request, cancellationToken).ConfigureAwait(false);
                await WriteResponseAsync(stream, response.StatusCode, response.Payload, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (ApiException ex)
            {
                await TryWriteErrorAsync(stream, ex.StatusCode, ex.Code, ex.Message, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (JsonException)
            {
                await TryWriteErrorAsync(stream, 400, "invalid_json", "Request JSON is invalid.", cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                // Add-in shutdown closes the connection.
            }
            catch (Exception ex)
            {
                // Do not return exception messages: they can contain request arguments or credentials.
                await TryWriteErrorAsync(
                        stream,
                        500,
                        "internal_error",
                        $"Internal bridge error ({ex.GetType().Name}).",
                        cancellationToken)
                    .ConfigureAwait(false);
            }
        }
    }

    private async Task<HttpResponseData> DispatchAsync(HttpRequestData request, CancellationToken cancellationToken)
    {
        Authenticate(request);
        var uri = ParseTarget(request.Target);
        var path = uri.AbsolutePath.TrimEnd('/');
        if (path.Length == 0)
        {
            path = "/";
        }

        if (request.Method == "GET" && path == "/v1/status")
        {
            var context = await ProContext.CaptureAsync(_events).ConfigureAwait(false);
            return Ok(new
            {
                ok = true,
                protocolVersion = BridgeOptions.ProtocolVersion,
                processId = Environment.ProcessId,
                port = _port,
                serverSessionId = _serverSessionId,
                writeEnabled = _options.AllowWrite,
                editCommandsEnabled = _options.AllowEditCommands,
                discardEditsEnabled = _options.AllowDiscardEdits,
                featureEditsEnabled = _options.AllowFeatureEdits,
                destructiveEditsEnabled = _options.AllowDestructive,
                gpAllowlistCount = _options.GpToolAllowlist.Count,
                typedGpContracts = GpJobController.ContractNames,
                gpOutputRootConfigured = _options.GpOutputRoot is not null,
                inputRootCount = _options.InputRoots.Count,
                projectRootCount = _options.ProjectRoots.Count,
                capabilities = new
                {
                    contextSnapshot = true,
                    setCamera = true,
                    zoomLayer = true,
                    refreshAndWaitForDraw = true,
                    featureEditOperation = true,
                    setActiveTime = true,
                    openStandaloneTable = true,
                    promptGeometry = false,
                    promptGeometryBlocker =
                        "A DAML MapTool has not been packaged or interaction-tested; this build does not claim prompt geometry support.",
                },
                context,
            });
        }

        if (request.Method == "POST" && path == "/v1/lease/acquire")
        {
            var body = Deserialize<AcquireLeaseRequest>(request);
            return await WithMutationGateAsync(async () =>
            {
                var lease = await _leases.AcquireAsync(body).ConfigureAwait(false);
                // A replacement for an expired lease must not leave its old work running.
                _jobs.CancelAll();
                return Ok(new { ok = true, lease });
            }, cancellationToken).ConfigureAwait(false);
        }

        if (request.Method == "POST" && path == "/v1/lease/renew")
        {
            return await WithMutationGateAsync(async () =>
            {
                var lease = await RequireLeaseAsync(request, renew: true).ConfigureAwait(false);
                return Ok(new { ok = true, lease });
            }, cancellationToken).ConfigureAwait(false);
        }

        if (request.Method == "POST" && path == "/v1/lease/release")
        {
            return await WithMutationGateAsync(() =>
            {
                _leases.Release(
                    Header(request, "X-ArcGIS-Pro-Session"),
                    Header(request, "X-ArcGIS-Pro-Lease"));
                _jobs.CancelAll();
                return Task.FromResult(Ok(new { ok = true }));
            }, cancellationToken).ConfigureAwait(false);
        }

        await RequireLeaseAsync(request, renew: false).ConfigureAwait(false);

        if (request.Method == "GET" && path == "/v1/context")
        {
            return Ok(new
            {
                ok = true,
                context = await ProContext.CaptureAsync(_events).ConfigureAwait(false),
            });
        }

        if (request.Method == "GET" && path == "/v1/events")
        {
            var after = QueryLong(uri, "after", 0, 0, long.MaxValue);
            var limit = (int)QueryLong(uri, "limit", 128, 1, 256);
            var waitMilliseconds = (int)QueryLong(uri, "waitMs", 0, 0, 30_000);
            var events = await _events.ReadAsync(after, limit, waitMilliseconds, cancellationToken)
                .ConfigureAwait(false);
            return Ok(new { ok = true, batch = events });
        }

        if (request.Method == "POST" && path == "/v1/jobs")
        {
            var body = Deserialize<StartGpJobRequest>(request);
            return await WithMutationGateAsync(async () =>
            {
                var lease = await RequireLeaseAsync(request, renew: false).ConfigureAwait(false);
                return Ok(new { ok = true, job = _jobs.Start(body, lease, _leases.IsCurrent) });
            }, cancellationToken).ConfigureAwait(false);
        }

        if (TryParseJobPath(path, out var jobId, out var cancel))
        {
            if (request.Method == "GET" && !cancel)
            {
                return Ok(new { ok = true, job = _jobs.Get(jobId) });
            }

            if (request.Method == "POST" && cancel)
            {
                var body = Deserialize<ConfirmedCommandRequest>(request);
                RequireWriteConfirmation(body);
                return await WithMutationGateAsync(async () =>
                {
                    await RequireLeaseAsync(request, renew: false).ConfigureAwait(false);
                    return Ok(new { ok = true, job = _jobs.Cancel(jobId) });
                }, cancellationToken).ConfigureAwait(false);
            }
        }

        if (request.Method == "GET" && path == "/v1/edit/status")
        {
            return Ok(new { ok = true, edit = await _edits.GetStatusAsync().ConfigureAwait(false) });
        }

        if (request.Method == "POST" && path.StartsWith("/v1/features/", StringComparison.Ordinal))
        {
            if (!_options.AllowWrite)
            {
                throw new ApiException(403, "write_disabled", "ARCGIS_PRO_MCP_ALLOW_WRITE is not enabled.");
            }

            if (!_options.AllowFeatureEdits)
            {
                throw new ApiException(
                    403,
                    "feature_edits_disabled",
                    "ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS is not enabled.");
            }

            if (path == "/v1/features/delete" && !_options.AllowDestructive)
            {
                throw new ApiException(
                    403,
                    "destructive_edits_disabled",
                    "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE is not enabled.");
            }

            if (_jobs.HasRunningJob)
            {
                throw new ApiException(409, "job_running", "Feature edits are disabled while a GP job is running.");
            }

            return await WithMutationGateAsync(async () =>
            {
                var lease = await RequireLeaseAsync(request, renew: false).ConfigureAwait(false);
                FeatureEditSnapshot result = path switch
                {
                    "/v1/features/create" => await _featureEdits.CreateAsync(
                            Deserialize<CreateFeatureRequest>(request))
                        .ConfigureAwait(false),
                    "/v1/features/modify" => await _featureEdits.ModifyAsync(
                            Deserialize<ModifyFeaturesRequest>(request))
                        .ConfigureAwait(false),
                    "/v1/features/delete" => await _featureEdits.DeleteAsync(
                            Deserialize<DeleteFeaturesRequest>(request))
                        .ConfigureAwait(false),
                    _ => throw new ApiException(404, "route_not_found", "Bridge route was not found."),
                };
                if (!_leases.IsCurrent(lease))
                {
                    throw new ApiException(
                        409,
                        "mutation_context_changed",
                        "The edit may have completed, but its project lease changed; refresh state before retrying.");
                }

                return Ok(new { ok = true, edit = result });
            }, cancellationToken).ConfigureAwait(false);
        }

        if (request.Method == "POST" && path.StartsWith("/v1/view/", StringComparison.Ordinal))
        {
            if (!_options.AllowWrite)
            {
                throw new ApiException(403, "write_disabled", "ARCGIS_PRO_MCP_ALLOW_WRITE is not enabled.");
            }

            return await WithMutationGateAsync(async () =>
            {
                var lease = await RequireLeaseAsync(request, renew: false).ConfigureAwait(false);
                ViewMutationSnapshot result = path switch
                {
                    "/v1/view/camera" => await _window.SetCameraAsync(Deserialize<SetCameraRequest>(request))
                        .ConfigureAwait(false),
                    "/v1/view/zoom-layer" => await _window.ZoomLayerAsync(Deserialize<ZoomLayerRequest>(request))
                        .ConfigureAwait(false),
                    "/v1/view/refresh" => await _window.RefreshAsync(
                            Deserialize<RefreshViewRequest>(request),
                            cancellationToken)
                        .ConfigureAwait(false),
                    "/v1/view/time" => await _window.SetActiveTimeAsync(
                            Deserialize<SetActiveTimeRequest>(request))
                        .ConfigureAwait(false),
                    "/v1/view/open-table" => await _window.OpenStandaloneTableAsync(
                            Deserialize<OpenStandaloneTableRequest>(request))
                        .ConfigureAwait(false),
                    _ => throw new ApiException(404, "route_not_found", "Bridge route was not found."),
                };
                if (!_leases.IsCurrent(lease))
                {
                    throw new ApiException(
                        409,
                        "mutation_context_changed",
                        "The command may have completed, but its project lease changed; refresh state before retrying.");
                }

                return Ok(new { ok = true, view = result });
            }, cancellationToken).ConfigureAwait(false);
        }

        if (request.Method == "POST" && path.StartsWith("/v1/edit/", StringComparison.Ordinal))
        {
            var body = Deserialize<ConfirmedCommandRequest>(request);
            RequireWriteConfirmation(body);
            if (!_options.AllowEditCommands)
            {
                throw new ApiException(
                    403,
                    "edit_commands_disabled",
                    "ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS is not enabled.");
            }

            if (path == "/v1/edit/discard" && !_options.AllowDiscardEdits)
            {
                throw new ApiException(
                    403,
                    "discard_edits_disabled",
                    "ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS is not enabled.");
            }
            if (path == "/v1/edit/discard" && !_options.AllowDestructive)
            {
                throw new ApiException(
                    403,
                    "destructive_edits_disabled",
                    "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE is not enabled.");
            }

            if (_jobs.HasRunningJob)
            {
                throw new ApiException(409, "job_running", "Editing commands are disabled while a GP job is running.");
            }

            return await WithMutationGateAsync(async () =>
            {
                var lease = await RequireLeaseAsync(request, renew: false).ConfigureAwait(false);
                if (!_leases.IsCurrent(lease))
                {
                    throw new ApiException(409, "lease_changed", "The project lease changed.");
                }

                _ = path switch
                {
                    "/v1/edit/undo" => await _edits.UndoAsync(body).ConfigureAwait(false),
                    "/v1/edit/redo" => await _edits.RedoAsync(body).ConfigureAwait(false),
                    "/v1/edit/save" => await _edits.SaveAsync(body).ConfigureAwait(false),
                    "/v1/edit/discard" => await _edits.DiscardAsync(body).ConfigureAwait(false),
                    _ => throw new ApiException(404, "route_not_found", "Bridge route was not found."),
                };
                if (!_leases.IsCurrent(lease))
                {
                    throw new ApiException(
                        409,
                        "mutation_context_changed",
                        "The command may have completed, but its project lease changed; refresh state before retrying.");
                }

                _events.MarkEditContextChanged("edit_command_completed");
                var edit = await _edits.GetStatusAsync().ConfigureAwait(false);
                return Ok(new { ok = true, edit });
            }, cancellationToken).ConfigureAwait(false);
        }

        throw new ApiException(404, "route_not_found", "Bridge route was not found.");
    }

    private void Authenticate(HttpRequestData request)
    {
        if (request.Headers.ContainsKey("Origin"))
        {
            throw new ApiException(403, "browser_origin_forbidden", "Browser-origin requests are not accepted.");
        }

        if (!string.Equals(Header(request, "Host"), $"127.0.0.1:{_port}", StringComparison.OrdinalIgnoreCase))
        {
            throw new ApiException(400, "invalid_host", "Host must match the discovered loopback endpoint.");
        }

        var authorization = Header(request, "Authorization");
        const string prefix = "Bearer ";
        if (authorization is null || !authorization.StartsWith(prefix, StringComparison.Ordinal) ||
            !SecurityTokens.FixedTimeEquals(authorization[prefix.Length..], _token))
        {
            throw new ApiException(401, "unauthorized", "A valid bearer token is required.");
        }
    }

    private async Task<LeaseSnapshot> RequireLeaseAsync(HttpRequestData request, bool renew)
    {
        try
        {
            return await _leases.ValidateAsync(
                    Header(request, "X-ArcGIS-Pro-Session"),
                    Header(request, "X-ArcGIS-Pro-Lease"),
                    renew)
                .ConfigureAwait(false);
        }
        catch (ApiException ex) when (ex.Code is "project_changed" or "lease_expired" or "lease_changed")
        {
            _jobs.CancelAll();
            throw;
        }
    }

    private void RequireWriteConfirmation(ConfirmedCommandRequest request)
    {
        if (!_options.AllowWrite)
        {
            throw new ApiException(403, "write_disabled", "ARCGIS_PRO_MCP_ALLOW_WRITE is not enabled.");
        }

        if (!request.Confirm)
        {
            throw new ApiException(400, "confirmation_required", "confirm must be true.");
        }
    }

    private async Task<HttpResponseData> WithMutationGateAsync(
        Func<Task<HttpResponseData>> action,
        CancellationToken cancellationToken)
    {
        if (!await _mutationGate.WaitAsync(TimeSpan.FromSeconds(2), cancellationToken).ConfigureAwait(false))
        {
            throw new ApiException(409, "bridge_busy", "Another bridge mutation is in progress.");
        }

        try
        {
            return await action().ConfigureAwait(false);
        }
        finally
        {
            _mutationGate.Release();
        }
    }

    private T Deserialize<T>(HttpRequestData request)
    {
        if (request.Body.Length == 0)
        {
            throw new ApiException(400, "body_required", "A JSON request body is required.");
        }

        var value = JsonSerializer.Deserialize<T>(request.Body, BridgeJson.Options);
        return value ?? throw new ApiException(400, "invalid_json", "Request JSON must be an object.");
    }

    private async Task<HttpRequestData> ReadRequestWithTimeoutAsync(
        NetworkStream stream,
        CancellationToken cancellationToken)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        deadline.CancelAfter(_options.RequestReadTimeout);
        try
        {
            return await ReadRequestAsync(stream, deadline.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new ApiException(408, "request_timeout", "HTTP request headers or body timed out.");
        }
    }

    private async Task<HttpRequestData> ReadRequestAsync(NetworkStream stream, CancellationToken cancellationToken)
    {
        var received = new List<byte>(4096);
        var buffer = new byte[4096];
        var headerEnd = -1;

        while (headerEnd < 0)
        {
            var count = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            if (count == 0)
            {
                throw new ApiException(400, "incomplete_request", "HTTP request ended before its headers.");
            }

            received.AddRange(buffer.AsSpan(0, count).ToArray());
            headerEnd = FindHeaderEnd(received);
            if (headerEnd < 0 && received.Count > _options.MaxHeaderBytes)
            {
                throw new ApiException(431, "headers_too_large", "HTTP headers exceed the bridge limit.");
            }
        }

        if (headerEnd > _options.MaxHeaderBytes)
        {
            throw new ApiException(431, "headers_too_large", "HTTP headers exceed the bridge limit.");
        }

        var headerText = Encoding.ASCII.GetString(received.Take(headerEnd).ToArray());
        var lines = headerText.Split("\r\n", StringSplitOptions.None);
        var requestLine = lines[0].Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (requestLine.Length != 3 || requestLine[2] != "HTTP/1.1")
        {
            throw new ApiException(400, "invalid_request_line", "Only HTTP/1.1 requests are supported.");
        }

        var method = requestLine[0].ToUpperInvariant();
        if (method is not ("GET" or "POST"))
        {
            throw new ApiException(405, "method_not_allowed", "Only GET and POST are supported.");
        }

        var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var line in lines.Skip(1))
        {
            var separator = line.IndexOf(':');
            if (separator <= 0)
            {
                throw new ApiException(400, "invalid_header", "HTTP header is malformed.");
            }

            var name = line[..separator].Trim();
            var value = line[(separator + 1)..].Trim();
            if (!headers.TryAdd(name, value))
            {
                throw new ApiException(400, "duplicate_header", "Duplicate HTTP headers are not accepted.");
            }
        }

        if (headers.ContainsKey("Transfer-Encoding"))
        {
            throw new ApiException(400, "transfer_encoding_forbidden", "Chunked requests are not supported.");
        }

        var contentLength = 0;
        if (headers.TryGetValue("Content-Length", out var contentLengthText) &&
            (!int.TryParse(contentLengthText, NumberStyles.None, CultureInfo.InvariantCulture, out contentLength) ||
             contentLength < 0))
        {
            throw new ApiException(400, "invalid_content_length", "Content-Length is invalid.");
        }

        if (contentLength > _options.MaxBodyBytes)
        {
            throw new ApiException(413, "body_too_large", "HTTP body exceeds the bridge limit.");
        }

        if (method == "GET" && contentLength != 0)
        {
            throw new ApiException(400, "get_body_forbidden", "GET requests must not contain a body.");
        }

        if (contentLength > 0 &&
            (!headers.TryGetValue("Content-Type", out var contentType) ||
             !contentType.StartsWith("application/json", StringComparison.OrdinalIgnoreCase)))
        {
            throw new ApiException(415, "json_required", "Content-Type must be application/json.");
        }

        var bodyOffset = headerEnd + 4;
        var alreadyRead = received.Count - bodyOffset;
        if (alreadyRead > contentLength)
        {
            throw new ApiException(400, "pipelining_forbidden", "HTTP pipelining is not supported.");
        }

        var body = new byte[contentLength];
        if (alreadyRead > 0)
        {
            received.CopyTo(bodyOffset, body, 0, alreadyRead);
        }

        var offset = alreadyRead;
        while (offset < body.Length)
        {
            var count = await stream.ReadAsync(body.AsMemory(offset), cancellationToken).ConfigureAwait(false);
            if (count == 0)
            {
                throw new ApiException(400, "incomplete_body", "HTTP request body ended early.");
            }

            offset += count;
        }

        return new HttpRequestData(method, requestLine[1], headers, body);
    }

    private static int FindHeaderEnd(IReadOnlyList<byte> bytes)
    {
        for (var index = Math.Max(0, bytes.Count - 8192); index <= bytes.Count - 4; index++)
        {
            if (bytes[index] == '\r' && bytes[index + 1] == '\n' &&
                bytes[index + 2] == '\r' && bytes[index + 3] == '\n')
            {
                return index;
            }
        }

        return -1;
    }

    private static Uri ParseTarget(string target)
    {
        if (!target.StartsWith("/", StringComparison.Ordinal) || target.StartsWith("//", StringComparison.Ordinal) ||
            target.Length > 4096 || !Uri.TryCreate("http://127.0.0.1" + target, UriKind.Absolute, out var uri))
        {
            throw new ApiException(400, "invalid_target", "HTTP request target is invalid.");
        }

        return uri;
    }

    private static long QueryLong(Uri uri, string name, long defaultValue, long minimum, long maximum)
    {
        foreach (var pair in uri.Query.TrimStart('?').Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var parts = pair.Split('=', 2);
            if (!string.Equals(Uri.UnescapeDataString(parts[0]), name, StringComparison.Ordinal))
            {
                continue;
            }

            var text = parts.Length == 2 ? Uri.UnescapeDataString(parts[1]) : string.Empty;
            if (!long.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out var value) ||
                value < minimum || value > maximum)
            {
                throw new ApiException(400, "invalid_query", $"Query parameter {name} is invalid.");
            }

            return value;
        }

        return defaultValue;
    }

    private static bool TryParseJobPath(string path, out string jobId, out bool cancel)
    {
        jobId = string.Empty;
        cancel = false;
        var parts = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length is not (3 or 4) || parts[0] != "v1" || parts[1] != "jobs")
        {
            return false;
        }

        jobId = parts[2];
        if (jobId.Length is < 8 or > 64 || jobId.Any(character => !char.IsLetterOrDigit(character) && character is not '-' and not '_'))
        {
            throw new ApiException(400, "invalid_job_id", "Job identifier is invalid.");
        }

        if (parts.Length == 4)
        {
            if (parts[3] != "cancel")
            {
                return false;
            }

            cancel = true;
        }

        return true;
    }

    private static string? Header(HttpRequestData request, string name) =>
        request.Headers.TryGetValue(name, out var value) ? value : null;

    private static HttpResponseData Ok(object payload) => new(200, payload);

    private static async Task WriteResponseAsync(
        NetworkStream stream,
        int statusCode,
        object payload,
        CancellationToken cancellationToken)
    {
        var body = JsonSerializer.SerializeToUtf8Bytes(payload, BridgeJson.Options);
        var header = Encoding.ASCII.GetBytes(
            $"HTTP/1.1 {statusCode} {StatusText(statusCode)}\r\n" +
            "Content-Type: application/json; charset=utf-8\r\n" +
            $"Content-Length: {body.Length}\r\n" +
            "Cache-Control: no-store\r\n" +
            "X-Content-Type-Options: nosniff\r\n" +
            "Connection: close\r\n\r\n");
        await stream.WriteAsync(header, cancellationToken).ConfigureAwait(false);
        await stream.WriteAsync(body, cancellationToken).ConfigureAwait(false);
    }

    private static async Task TryWriteErrorAsync(
        NetworkStream stream,
        int statusCode,
        string code,
        string message,
        CancellationToken cancellationToken)
    {
        try
        {
            await WriteResponseAsync(
                    stream,
                    statusCode,
                    new { ok = false, error = new { code, message } },
                    cancellationToken)
                .ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is IOException or SocketException or OperationCanceledException or ObjectDisposedException)
        {
            // The peer disconnected; there is intentionally no request logging.
        }
    }

    private static string StatusText(int statusCode) => statusCode switch
    {
        200 => "OK",
        400 => "Bad Request",
        401 => "Unauthorized",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        408 => "Request Timeout",
        409 => "Conflict",
        413 => "Content Too Large",
        415 => "Unsupported Media Type",
        431 => "Request Header Fields Too Large",
        503 => "Service Unavailable",
        _ => "Internal Server Error",
    };

    public async ValueTask DisposeAsync()
    {
        _shutdown.Cancel();
        _listener?.Stop();
        var clientsStopped = true;
        if (_acceptLoop is not null)
        {
            try
            {
                await _acceptLoop.ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is OperationCanceledException or SocketException)
            {
                // Listener shutdown.
            }
        }

        try
        {
            await Task.WhenAll(_clients.Values).WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is OperationCanceledException or TimeoutException)
        {
            clientsStopped = false;
        }

        _discoveryFile?.Dispose();
        _discoveryFile = null;
        _listener = null;
        if (clientsStopped)
        {
            _mutationGate.Dispose();
            _clientSlots.Dispose();
            _shutdown.Dispose();
        }
    }

    private sealed record HttpResponseData(int StatusCode, object Payload);
}
