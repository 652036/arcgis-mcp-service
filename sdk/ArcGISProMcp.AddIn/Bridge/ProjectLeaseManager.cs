namespace ArcGISProMcp.AddIn.Bridge;

internal sealed class ProjectLeaseManager
{
    private readonly object _gate = new();
    private readonly string _serverSessionId;
    private readonly TimeSpan _defaultTtl;
    private readonly IReadOnlyList<string> _projectRoots;
    private LeaseSnapshot? _lease;
    private long _generation;

    internal ProjectLeaseManager(
        string serverSessionId,
        TimeSpan defaultTtl,
        IReadOnlyList<string> projectRoots)
    {
        _serverSessionId = serverSessionId;
        _defaultTtl = defaultTtl;
        _projectRoots = projectRoots;
    }

    internal async Task<LeaseSnapshot> AcquireAsync(AcquireLeaseRequest request)
    {
        RequireServerSession(request.ServerSessionId);

        var expected = ProjectIdentity.Canonicalize(request.ExpectedProjectUri);
        if (string.IsNullOrEmpty(expected))
        {
            throw new ApiException(400, "expected_project_required", "expectedProjectUri is required.");
        }

        PathPolicy.ValidateProject(expected, _projectRoots);
        var observedGeneration = ReadGeneration();
        var currentProjectUri = await ProContext.CaptureProjectUriAsync().ConfigureAwait(false);
        if (string.IsNullOrEmpty(currentProjectUri))
        {
            throw new ApiException(409, "no_saved_project", "A saved ArcGIS Pro project must be open.");
        }

        if (!ProjectIdentity.Equals(expected, currentProjectUri))
        {
            throw new ApiException(409, "project_mismatch", "The open project does not match expectedProjectUri.");
        }

        var requestedSeconds = request.TtlSeconds ?? (int)_defaultTtl.TotalSeconds;
        var ttl = TimeSpan.FromSeconds(Math.Clamp(requestedSeconds, 10, 120));
        lock (_gate)
        {
            if (_generation != observedGeneration)
            {
                throw new ApiException(409, "project_changed", "The project changed while acquiring the lease.");
            }

            if (_lease is not null && _lease.ExpiresAtUtc > DateTimeOffset.UtcNow)
            {
                throw new ApiException(409, "lease_held", "The current project already has an active bridge lease.");
            }

            _generation++;
            _lease = new LeaseSnapshot(
                SecurityTokens.Create(),
                _serverSessionId,
                currentProjectUri,
                _generation,
                DateTimeOffset.UtcNow.Add(ttl));
            return _lease;
        }
    }

    internal async Task<LeaseSnapshot> ValidateAsync(string? serverSessionId, string? leaseId, bool renew)
    {
        RequireServerSession(serverSessionId);

        LeaseSnapshot candidate;
        lock (_gate)
        {
            candidate = RequireCurrentLeaseLocked(leaseId);
        }

        var currentProjectUri = await ProContext.CaptureProjectUriAsync().ConfigureAwait(false);
        lock (_gate)
        {
            var latest = RequireCurrentLeaseLocked(leaseId);
            if (latest.Generation != candidate.Generation)
            {
                throw new ApiException(409, "lease_changed", "The project lease changed.");
            }

            if (!ProjectIdentity.Equals(latest.ProjectUri, currentProjectUri))
            {
                _generation++;
                _lease = null;
                throw new ApiException(409, "project_changed", "The open project changed; acquire a new lease.");
            }

            if (renew)
            {
                _lease = latest with { ExpiresAtUtc = DateTimeOffset.UtcNow.Add(_defaultTtl) };
            }

            return _lease ?? latest;
        }
    }

    internal void Release(string? serverSessionId, string? leaseId)
    {
        RequireServerSession(serverSessionId);
        lock (_gate)
        {
            _ = RequireCurrentLeaseLocked(leaseId);
            _generation++;
            _lease = null;
        }
    }

    internal bool IsCurrent(LeaseSnapshot candidate)
    {
        lock (_gate)
        {
            return _lease is not null &&
                   _lease.ExpiresAtUtc > DateTimeOffset.UtcNow &&
                   _lease.Generation == candidate.Generation &&
                   SecurityTokens.FixedTimeEquals(_lease.LeaseId, candidate.LeaseId) &&
                   SecurityTokens.FixedTimeEquals(_lease.ServerSessionId, candidate.ServerSessionId);
        }
    }

    internal void Invalidate()
    {
        lock (_gate)
        {
            _generation++;
            _lease = null;
        }
    }

    private long ReadGeneration()
    {
        lock (_gate)
        {
            return _generation;
        }
    }

    private void RequireServerSession(string? serverSessionId)
    {
        if (!SecurityTokens.FixedTimeEquals(serverSessionId, _serverSessionId))
        {
            throw new ApiException(409, "session_changed", "The add-in session changed; rediscover the bridge.");
        }
    }

    private LeaseSnapshot RequireCurrentLeaseLocked(string? leaseId)
    {
        if (_lease is null || !SecurityTokens.FixedTimeEquals(leaseId, _lease.LeaseId))
        {
            throw new ApiException(409, "invalid_lease", "A valid project lease is required.");
        }

        if (_lease.ExpiresAtUtc <= DateTimeOffset.UtcNow)
        {
            _generation++;
            _lease = null;
            throw new ApiException(409, "lease_expired", "The project lease expired.");
        }

        return _lease;
    }
}
