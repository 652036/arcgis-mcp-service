using System.Globalization;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Editing;
using ArcGIS.Desktop.Editing.Attributes;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using ArcGISProMcp.AddIn.Bridge;
using ArcGISProMcp.AddIn.Events;

namespace ArcGISProMcp.AddIn.Editing;

internal sealed class FeatureEditService
{
    private const long MaximumExactBigInteger = 9_007_199_254_740_991;
    private const int MaximumAttributes = 64;
    private const int MaximumModifiedFeatures = 100;
    private const int MaximumDeletedFeatures = 1_000;
    private const int MaximumVertices = 10_000;
    private readonly ProEventController _events;

    internal FeatureEditService(ProEventController events)
    {
        _events = events;
    }

    internal Task<FeatureEditSnapshot> CreateAsync(CreateFeatureRequest request) =>
        ExecuteAsync("create", request, null);

    internal Task<FeatureEditSnapshot> ModifyAsync(ModifyFeaturesRequest request) =>
        ExecuteAsync("modify", request, request);

    internal Task<FeatureEditSnapshot> DeleteAsync(DeleteFeaturesRequest request) =>
        ExecuteAsync("delete", request, request);

    private async Task<FeatureEditSnapshot> ExecuteAsync(
        string operationName,
        FeatureEditRequest request,
        SelectedFeatureEditRequest? selectedRequest)
    {
        ValidateBaseRequest(request);
        ValidateSelectionRequest(operationName, selectedRequest);
        var result = await QueuedTask.Run(() => ExecuteOnMct(operationName, request, selectedRequest))
            .ConfigureAwait(false);
        if (!result.Succeeded)
        {
            throw new ApiException(409, "edit_operation_failed", "The ArcGIS Pro EditOperation failed atomically.");
        }

        _events.MarkEditContextChanged($"feature_{operationName}_completed");
        return new FeatureEditSnapshot(
            operationName,
            request.LayerUri,
            result.AffectedCount,
            result.OidDigest,
            await ProContext.CaptureAsync(_events).ConfigureAwait(false));
    }

    private EditExecutionResult ExecuteOnMct(
        string operationName,
        FeatureEditRequest request,
        SelectedFeatureEditRequest? selectedRequest)
    {
        RequireGenerations(request, selectedRequest);
        var project = Project.Current ??
                      throw new ApiException(409, "no_project", "No ArcGIS Pro project is open.");
        if (project.ReadOnly)
        {
            throw new ApiException(409, "project_read_only", "The current ArcGIS Pro project is read-only.");
        }

        if (!project.IsEditingEnabled)
        {
            throw new ApiException(409, "editing_disabled", "Editing is disabled in the current ArcGIS Pro project.");
        }

        var view = LayoutView.Active?.ActivatedMapView ?? MapView.Active;
        if (view?.Map is null)
        {
            throw new ApiException(409, "no_active_map", "An active map view is required for feature editing.");
        }

        if (!string.Equals(view.Map.URI, request.ExpectedMapUri, StringComparison.Ordinal))
        {
            throw new ApiException(409, "map_changed", "The active map does not match expectedMapUri.");
        }

        var layers = view.Map.GetLayersAsFlattenedList()
            .OfType<FeatureLayer>()
            .Where(layer => string.Equals(layer.URI, request.LayerUri, StringComparison.Ordinal))
            .ToArray();
        if (layers.Length != 1)
        {
            throw new ApiException(
                layers.Length == 0 ? 404 : 409,
                layers.Length == 0 ? "feature_layer_not_found" : "layer_uri_ambiguous",
                "layerUri must identify exactly one FeatureLayer in the active map.");
        }

        var layer = layers[0];
        if (!layer.CanEditData())
        {
            throw new ApiException(409, "layer_not_editable", "The target feature layer is not editable.");
        }

        var selectedIds = selectedRequest is null
            ? Array.Empty<long>()
            : GetAndValidateSelectedIds(view, layer, selectedRequest, operationName);
        var attributes = ValidateAttributes(layer, request switch
        {
            CreateFeatureRequest create => create.Attributes,
            ModifyFeaturesRequest modify => modify.Attributes,
            _ => null,
        });
        var geometryRequest = request switch
        {
            CreateFeatureRequest create => create.Geometry,
            ModifyFeaturesRequest modify => modify.Geometry,
            _ => null,
        };
        var geometry = geometryRequest is null ? null : BuildGeometry(layer, geometryRequest);

        if (operationName == "create" && geometry is null)
        {
            throw new ApiException(400, "geometry_required", "geometry is required for feature creation.");
        }

        if (operationName == "modify" && attributes.Count == 0 && geometry is null)
        {
            throw new ApiException(400, "edit_values_required", "attributes or geometry is required for modify.");
        }

        if (operationName == "modify" && geometry is not null && selectedIds.Length != 1)
        {
            throw new ApiException(
                400,
                "single_geometry_target_required",
                "A geometry modification requires exactly one selected feature.");
        }

        var edit = new EditOperation
        {
            Name = operationName switch
            {
                "create" => "ArcGIS Pro MCP: create feature",
                "modify" => "ArcGIS Pro MCP: modify selected features",
                _ => "ArcGIS Pro MCP: delete selected features",
            },
            SelectNewFeatures = false,
            SelectModifiedFeatures = false,
            // Fail before committing when the target only supports short/direct transactions.
            // This endpoint promises a native undo-stack entry rather than an immediate commit.
            EditOperationType = EditOperationType.Long,
            ShowModalMessageAfterFailure = false,
            ShowProgressor = false,
        };

        switch (operationName)
        {
            case "create":
                attributes["SHAPE"] = geometry!;
                edit.Create(layer, attributes);
                break;
            case "modify":
                if (geometry is not null)
                {
                    attributes["SHAPE"] = geometry;
                }

                foreach (var objectId in selectedIds)
                {
                    edit.Modify(layer, objectId, attributes);
                }

                break;
            case "delete":
                edit.Delete(layer, selectedIds);
                break;
            default:
                throw new ApiException(500, "invalid_edit_operation", "Unsupported internal edit operation.");
        }

        if (edit.IsEmpty)
        {
            throw new ApiException(409, "edit_operation_empty", "The requested EditOperation contains no changes.");
        }

        RequireGenerations(request, selectedRequest);
        var succeeded = edit.Execute();
        return new EditExecutionResult(
            succeeded,
            operationName == "create" ? 1 : selectedIds.Length,
            selectedRequest?.ExpectedOidDigest.ToLowerInvariant());
    }

    private long[] GetAndValidateSelectedIds(
        MapView view,
        FeatureLayer layer,
        SelectedFeatureEditRequest request,
        string operationName)
    {
        var dictionary = view.Map.GetSelection().ToDictionary<FeatureLayer>();
        var matches = dictionary
            .Where(item => string.Equals(item.Key.URI, layer.URI, StringComparison.Ordinal))
            .ToArray();
        if (matches.Length > 1)
        {
            throw new ApiException(409, "selection_layer_ambiguous", "The selected feature layer URI is ambiguous.");
        }

        var ids = (matches.FirstOrDefault().Value ?? new List<long>())
            .Distinct()
            .OrderBy(value => value)
            .ToArray();
        var limit = operationName == "delete" ? MaximumDeletedFeatures : MaximumModifiedFeatures;
        if (ids.Length == 0 || ids.Length > limit)
        {
            throw new ApiException(
                400,
                "selection_count_out_of_range",
                $"The target layer selection must contain between 1 and {limit} features.");
        }

        if (ids.Length != request.ExpectedCount)
        {
            throw new ApiException(409, "selection_count_changed", "The selected feature count changed.");
        }

        var digest = OidSelectionDigest.ComputeLayer(layer.URI, ids);
        if (!SecurityTokens.FixedTimeEquals(digest, request.ExpectedOidDigest.ToLowerInvariant()))
        {
            throw new ApiException(409, "selection_digest_changed", "The selected feature OID digest changed.");
        }

        return ids;
    }

    private static Dictionary<string, object> ValidateAttributes(
        FeatureLayer layer,
        IReadOnlyDictionary<string, JsonElement>? requested)
    {
        if (requested is null)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal);
        }

        if (requested.Count > MaximumAttributes)
        {
            throw new ApiException(400, "too_many_attributes", $"At most {MaximumAttributes} attributes are allowed.");
        }

        var inspector = new Inspector();
        inspector.LoadSchema(layer);
        var schema = inspector.ToArray();
        var result = new Dictionary<string, object>(StringComparer.Ordinal);
        foreach (var pair in requested)
        {
            if (pair.Key.Length is 0 or > 128)
            {
                throw new ApiException(400, "invalid_field_name", "Attribute field names must be bounded.");
            }

            var matches = schema.Where(attribute => string.Equals(
                    attribute.FieldName,
                    pair.Key,
                    StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
            {
                throw new ApiException(400, "field_not_found", "Every attribute key must exactly match one field.");
            }

            var attribute = matches[0];
            if (!attribute.IsEditable || attribute.IsSystemField || attribute.IsGeometryField)
            {
                throw new ApiException(403, "field_not_editable", "System, geometry, and non-editable fields are forbidden.");
            }

            result.Add(pair.Key, ConvertAttributeValue(pair.Value, attribute));
        }

        return result;
    }

    private static object ConvertAttributeValue(JsonElement value, ArcGIS.Desktop.Editing.Attributes.Attribute attribute)
    {
        if (value.ValueKind == JsonValueKind.Null)
        {
            if (!attribute.IsNullable)
            {
                throw new ApiException(400, "null_not_allowed", "A non-nullable field cannot be set to null.");
            }

            return DBNull.Value;
        }

        if (attribute.FieldType is FieldType.OID or FieldType.Geometry or FieldType.Blob or
            FieldType.Raster or FieldType.GlobalID or FieldType.XML or FieldType.DateOnly or
            FieldType.TimeOnly)
        {
            throw new ApiException(
                403,
                "field_type_forbidden",
                "System, binary, geometry, raster, XML, date-only, and time-only fields are forbidden.");
        }

        if (attribute.FieldType is FieldType.Date or FieldType.TimestampOffset)
        {
            if (value.ValueKind != JsonValueKind.String ||
                !TryParseExplicitOffset(value.GetString(), out var timestamp))
            {
                throw new ApiException(
                    400,
                    "invalid_date_value",
                    "Date attributes must be ISO-8601 strings with Z or an explicit UTC offset.");
            }

            return attribute.FieldType == FieldType.TimestampOffset ? timestamp : timestamp.UtcDateTime;
        }

        if (attribute.FieldType == FieldType.GUID)
        {
            var text = value.ValueKind == JsonValueKind.String ? value.GetString() : null;
            if (!Guid.TryParse(text, out var guid) ||
                !string.Equals(guid.ToString("D"), text, StringComparison.OrdinalIgnoreCase))
            {
                throw new ApiException(400, "invalid_guid_value", "GUID attributes must be canonical GUID strings.");
            }

            return guid;
        }

        if (attribute.FieldType == FieldType.String)
        {
            if (value.ValueKind != JsonValueKind.String)
            {
                throw new ApiException(400, "invalid_string_value", "String fields require JSON strings.");
            }

            var text = value.GetString() ?? string.Empty;
            if (text.Length > 8_192)
            {
                throw new ApiException(400, "attribute_value_too_long", "String attribute values are limited to 8192 characters.");
            }

            return text;
        }

        if (attribute.FieldType is FieldType.SmallInteger or FieldType.Integer or FieldType.BigInteger)
        {
            if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt64(out var integer))
            {
                throw new ApiException(400, "invalid_integer_value", "Integer fields require JSON integers.");
            }

            if (attribute.FieldType == FieldType.SmallInteger)
            {
                if (integer is < short.MinValue or > short.MaxValue)
                {
                    throw new ApiException(400, "attribute_out_of_range", "A small-integer attribute is out of range.");
                }

                return (short)integer;
            }

            if (attribute.FieldType == FieldType.Integer)
            {
                if (integer is < int.MinValue or > int.MaxValue)
                {
                    throw new ApiException(400, "attribute_out_of_range", "An integer attribute is out of range.");
                }

                return (int)integer;
            }

            if (integer is < -MaximumExactBigInteger or > MaximumExactBigInteger)
            {
                throw new ApiException(400, "attribute_out_of_range", "A big-integer attribute is out of range.");
            }

            return integer;
        }

        if (attribute.FieldType is FieldType.Single or FieldType.Double)
        {
            if (value.ValueKind != JsonValueKind.Number)
            {
                throw new ApiException(400, "invalid_numeric_value", "Floating-point fields require JSON numbers.");
            }

            var number = value.GetDouble();
            if (!double.IsFinite(number) ||
                (attribute.FieldType == FieldType.Single && Math.Abs(number) > float.MaxValue))
            {
                throw new ApiException(400, "invalid_numeric_value", "Numeric attributes must be finite and in range.");
            }

            return attribute.FieldType == FieldType.Single ? (object)(float)number : number;
        }

        throw new ApiException(400, "attribute_type_mismatch", "The JSON value does not match the target field type.");
    }

    private static bool TryParseExplicitOffset(string? value, out DateTimeOffset timestamp)
    {
        timestamp = default;
        if (string.IsNullOrEmpty(value) || value.Length > 64)
        {
            return false;
        }

        var timeSeparator = value.IndexOf('T');
        var hasExplicitOffset = value.EndsWith("Z", StringComparison.OrdinalIgnoreCase) ||
                                (timeSeparator >= 0 &&
                                 (value.LastIndexOf('+') > timeSeparator || value.LastIndexOf('-') > timeSeparator));
        return hasExplicitOffset && DateTimeOffset.TryParse(
            value,
            CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind,
            out timestamp);
    }

    private static Geometry BuildGeometry(FeatureLayer layer, GeometryRequest request)
    {
        var type = (request.Type ?? string.Empty).Trim().ToLowerInvariant();
        if (type is not ("point" or "polyline" or "polygon"))
        {
            throw new ApiException(400, "geometry_type_forbidden", "Only point, polyline, and polygon geometries are supported.");
        }

        var coordinates = request.Coordinates;
        if (coordinates is null || coordinates.Length == 0 || coordinates.Length > MaximumVertices)
        {
            throw new ApiException(400, "invalid_geometry", $"Geometry requires 1 to {MaximumVertices} two-dimensional vertices.");
        }

        foreach (var coordinate in coordinates)
        {
            if (coordinate is null || coordinate.Length != 2 || coordinate.Any(value => !double.IsFinite(value)))
            {
                throw new ApiException(400, "invalid_geometry", "Every vertex must contain exactly two finite numbers.");
            }
        }

        using var featureClass = layer.GetFeatureClass() ??
                                 throw new ApiException(409, "feature_class_unavailable", "The target feature class is unavailable.");
        using var definition = featureClass.GetDefinition();
        var spatialReference = definition.GetSpatialReference();
        if (spatialReference is null || request.SpatialReferenceWkid <= 0 ||
            spatialReference.Wkid != request.SpatialReferenceWkid)
        {
            throw new ApiException(
                400,
                "spatial_reference_mismatch",
                "Geometry WKID must exactly match the target feature class spatial reference.");
        }

        var expectedType = layer.ShapeType.ToString();
        if (!expectedType.EndsWith(type, StringComparison.OrdinalIgnoreCase))
        {
            throw new ApiException(400, "geometry_type_mismatch", "Geometry type does not match the target feature layer.");
        }

        var points = coordinates
            .Select(coordinate => MapPointBuilderEx.CreateMapPoint(coordinate[0], coordinate[1], spatialReference))
            .ToArray();
        return type switch
        {
            "point" when points.Length == 1 => points[0],
            "point" => throw new ApiException(400, "invalid_point", "Point geometry requires exactly one vertex."),
            "polyline" when points.Length >= 2 => PolylineBuilderEx.CreatePolyline(points, spatialReference),
            "polyline" => throw new ApiException(400, "invalid_polyline", "Polyline geometry requires at least two vertices."),
            "polygon" when points.Length >= 4 && CoordinatesEqual(coordinates[0], coordinates[^1]) =>
                PolygonBuilderEx.CreatePolygon(points, spatialReference),
            "polygon" => throw new ApiException(
                400,
                "invalid_polygon",
                "Polygon geometry requires at least four vertices and an explicitly closed ring."),
            _ => throw new ApiException(400, "invalid_geometry", "Geometry is invalid."),
        };
    }

    private static bool CoordinatesEqual(IReadOnlyList<double> left, IReadOnlyList<double> right) =>
        left[0].Equals(right[0]) && left[1].Equals(right[1]);

    private void ValidateBaseRequest(FeatureEditRequest request)
    {
        if (!request.Confirm)
        {
            throw new ApiException(400, "confirmation_required", "confirm must be true.");
        }

        if (string.IsNullOrWhiteSpace(request.ExpectedMapUri) || request.ExpectedMapUri.Length > 4096 ||
            string.IsNullOrWhiteSpace(request.LayerUri) || request.LayerUri.Length > 4096)
        {
            throw new ApiException(400, "edit_target_required", "expectedMapUri and layerUri are required and bounded.");
        }

        if (request.ExpectedContextGeneration is null or < 0 || request.ExpectedEditGeneration is null or < 0)
        {
            throw new ApiException(
                400,
                "edit_generation_required",
                "expectedContextGeneration and expectedEditGeneration from the latest context are required.");
        }

        RequireGenerations(request, null);
    }

    private static void ValidateSelectionRequest(string operationName, SelectedFeatureEditRequest? request)
    {
        if (request is null)
        {
            return;
        }

        if (request.ExpectedSelectionGeneration is null or < 0 || request.ExpectedCount is null or < 1)
        {
            throw new ApiException(
                400,
                "selection_precondition_required",
                "expectedSelectionGeneration and a positive expectedCount are required.");
        }

        if (request.ExpectedOidDigest.Length != 64 ||
            request.ExpectedOidDigest.Any(character => !Uri.IsHexDigit(character)))
        {
            throw new ApiException(400, "invalid_oid_digest", "expectedOidDigest must be a SHA-256 hex digest.");
        }

        if (operationName == "delete" && request is DeleteFeaturesRequest delete && !delete.ConfirmDeleteSelection)
        {
            throw new ApiException(
                400,
                "delete_selection_confirmation_required",
                "confirmDeleteSelection must be true.");
        }
    }

    private void RequireGenerations(FeatureEditRequest request, SelectedFeatureEditRequest? selectedRequest)
    {
        if (_events.ContextGeneration != request.ExpectedContextGeneration ||
            _events.EditGeneration != request.ExpectedEditGeneration ||
            (selectedRequest is not null &&
             _events.SelectionGeneration != selectedRequest.ExpectedSelectionGeneration))
        {
            throw new ApiException(409, "edit_context_changed", "The map, selection, or edit context changed; refresh context and retry.");
        }
    }

    private sealed record EditExecutionResult(bool Succeeded, int AffectedCount, string? OidDigest);
}
