using System.Security.AccessControl;
using System.Security.Principal;

namespace ArcGISProMcp.AddIn.Bridge;

internal sealed class DiscoveryFile : IDisposable
{
    private readonly string _path;

    private DiscoveryFile(string path)
    {
        _path = path;
    }

    internal static async Task<DiscoveryFile> CreateAsync(
        string directory,
        DiscoveryDocument document,
        CancellationToken cancellationToken)
    {
        EnsurePrivateDirectory(directory);
        var path = Path.Combine(directory, $"bridge-{document.ProcessId}.json");
        var temporaryPath = path + ".tmp-" + SecurityTokens.Create(8);
        var json = JsonSerializer.Serialize(document, BridgeJson.Options);

        await File.WriteAllTextAsync(temporaryPath, json, cancellationToken).ConfigureAwait(false);
        RestrictFileToCurrentUser(temporaryPath);
        File.Move(temporaryPath, path, true);
        RestrictFileToCurrentUser(path);
        return new DiscoveryFile(path);
    }

    private static void EnsurePrivateDirectory(string path)
    {
        Directory.CreateDirectory(path);
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var sid = WindowsIdentity.GetCurrent().User ??
                  throw new InvalidOperationException("Current Windows identity has no SID.");
        var security = new DirectorySecurity();
        security.SetOwner(sid);
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        security.AddAccessRule(new FileSystemAccessRule(
            sid,
            FileSystemRights.FullControl,
            InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit,
            PropagationFlags.None,
            AccessControlType.Allow));
        new DirectoryInfo(path).SetAccessControl(security);
    }

    private static void RestrictFileToCurrentUser(string path)
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var sid = WindowsIdentity.GetCurrent().User ??
                  throw new InvalidOperationException("Current Windows identity has no SID.");
        var security = new FileSecurity();
        security.SetOwner(sid);
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        security.AddAccessRule(new FileSystemAccessRule(
            sid,
            FileSystemRights.FullControl,
            AccessControlType.Allow));
        new FileInfo(path).SetAccessControl(security);
    }

    public void Dispose()
    {
        try
        {
            File.Delete(_path);
        }
        catch (IOException)
        {
            // The token is short-lived and rotates on every add-in session.
        }
        catch (UnauthorizedAccessException)
        {
            // Do not emit a log containing the discovery path or token.
        }
    }
}
