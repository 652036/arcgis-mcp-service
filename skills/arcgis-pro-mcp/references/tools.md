# ArcGIS Pro MCP Tool Reference

## Environment And Discovery

- `arcgis_pro_environment_info`
- `arcgis_pro_server_capabilities`
- `arcgis_pro_list_projects`

## Project, Map, Layer, And Layout Read-Only

- `arcgis_pro_project_summary`, `arcgis_pro_project_connections`
- `arcgis_pro_list_maps`, `arcgis_pro_list_layouts`, `arcgis_pro_list_reports`
- `arcgis_pro_list_layers`, `arcgis_pro_list_tables`, `arcgis_pro_list_bookmarks`
- `arcgis_pro_map_spatial_reference`, `arcgis_pro_map_camera`, `arcgis_pro_mapframe_extent`
- `arcgis_pro_layer_properties`, `arcgis_pro_list_layout_elements`, `arcgis_pro_list_layout_map_frames`
- `arcgis_pro_list_broken_sources`, `arcgis_pro_get_layer_extent`

## Workspace, Dataset, And Metadata

- `arcgis_pro_describe`, `arcgis_pro_list_fields`
- `arcgis_pro_workspace_list_feature_classes`, `arcgis_pro_workspace_list_rasters`, `arcgis_pro_workspace_list_tables`
- `arcgis_pro_workspace_list_datasets`, `arcgis_pro_workspace_list_feature_datasets`, `arcgis_pro_workspace_list_domains`
- `arcgis_pro_list_sde_datasets`
- `arcgis_pro_get_metadata`, `arcgis_pro_set_metadata`

## Data Access

- Read: `arcgis_pro_da_table_sample`, `arcgis_pro_da_query_rows`, `arcgis_pro_da_distinct_values`
- Write: `arcgis_pro_da_update_field_constant`, `arcgis_pro_da_insert_features`, `arcgis_pro_da_update_features`, `arcgis_pro_da_delete_selected`

## Map And Layer Writes

- Project/save: `arcgis_pro_save_project`, `arcgis_pro_save_project_copy`
- Layers/tables: `arcgis_pro_add_layer_from_path`, `arcgis_pro_remove_layer`, `arcgis_pro_add_table_from_path`, `arcgis_pro_remove_table`
- State: `arcgis_pro_set_layer_visible`, `arcgis_pro_set_layer_transparency`, `arcgis_pro_set_definition_query`, `arcgis_pro_set_layer_scale_range`
- Selection: `arcgis_pro_select_layer_by_attribute`, `arcgis_pro_select_layer_by_location`, `arcgis_pro_clear_map_selection`, `arcgis_pro_layer_selection_count`, `arcgis_pro_layer_selection_fids`
- Temporary views: `arcgis_pro_make_feature_layer`, `arcgis_pro_make_table_view`
- Organization: `arcgis_pro_create_group_layer`, `arcgis_pro_move_layer`, `arcgis_pro_rename_layer`
- Data sources: `arcgis_pro_layer_replace_data_source`, `arcgis_pro_repair_layer_source`, `arcgis_pro_create_db_connection`
- Maps: `arcgis_pro_create_map`, `arcgis_pro_duplicate_map`, `arcgis_pro_remove_map`, `arcgis_pro_rename_map`, `arcgis_pro_add_basemap`, `arcgis_pro_set_map_spatial_reference`, `arcgis_pro_set_map_reference_scale`, `arcgis_pro_set_map_default_camera`, `arcgis_pro_map_pan_to_extent`, `arcgis_pro_set_time_slider`

## Layouts, Map Frames, And Exports

- Exports: `arcgis_pro_export_layout_pdf`, `arcgis_pro_export_layout_image`, `arcgis_pro_export_report_pdf`, `arcgis_pro_export_map_to_image`
- Layout edits: `arcgis_pro_update_layout_text_element`, `arcgis_pro_create_layout`, `arcgis_pro_rename_layout`, `arcgis_pro_remove_layout`
- Map frames and views: `arcgis_pro_set_mapframe_extent`, `arcgis_pro_mapframe_zoom_to_bookmark`, `arcgis_pro_zoom_to_layer`, `arcgis_pro_zoom_to_selection`
- Layout elements: `arcgis_pro_set_layout_element_position`, `arcgis_pro_set_layout_element_visible`, `arcgis_pro_update_legend_items`

## Symbology And Labels

- `arcgis_pro_apply_symbology_from_layer`
- `arcgis_pro_set_unique_value_renderer`, `arcgis_pro_set_graduated_colors_renderer`, `arcgis_pro_set_graduated_symbols_renderer`, `arcgis_pro_set_simple_renderer`, `arcgis_pro_set_heatmap_renderer`
- `arcgis_pro_update_label_expression`, `arcgis_pro_set_label_font`, `arcgis_pro_toggle_layer_labels`
- `arcgis_pro_list_layer_renderers`, `arcgis_pro_update_layer_cim`, `arcgis_pro_layer_add_field_alias`

## Named GP Wrappers

- Query/schema: `arcgis_pro_gp_list_registered`, `arcgis_pro_gp_get_count`, `arcgis_pro_gp_get_raster_property`, `arcgis_pro_gp_get_cell_value`, `arcgis_pro_gp_test_schema_lock`
- Vector analysis: `arcgis_pro_gp_buffer`, `arcgis_pro_gp_clip`, `arcgis_pro_gp_analysis_select`, `arcgis_pro_gp_dissolve`, `arcgis_pro_gp_intersect`, `arcgis_pro_gp_union`, `arcgis_pro_gp_erase`, `arcgis_pro_gp_spatial_join`, `arcgis_pro_gp_near`, `arcgis_pro_gp_generate_near_table`, `arcgis_pro_gp_identity`, `arcgis_pro_gp_symmetrical_difference`, `arcgis_pro_gp_count_overlapping_features`
- Geometry/conversion: `arcgis_pro_gp_feature_to_point`, `arcgis_pro_gp_feature_to_line`, `arcgis_pro_gp_points_to_line`, `arcgis_pro_gp_polygon_to_line`, `arcgis_pro_gp_minimum_bounding_geometry`, `arcgis_pro_gp_convex_hull`, `arcgis_pro_gp_multipart_to_singlepart`, `arcgis_pro_gp_aggregate_polygons`
- Data management: `arcgis_pro_gp_copy_features`, `arcgis_pro_gp_copy_feature_class`, `arcgis_pro_gp_create_feature_class`, `arcgis_pro_gp_create_table`, `arcgis_pro_gp_create_file_gdb`, `arcgis_pro_gp_create_feature_dataset`, `arcgis_pro_gp_rename_dataset`, `arcgis_pro_gp_delete_dataset`, `arcgis_pro_gp_append`, `arcgis_pro_gp_delete_features`, `arcgis_pro_gp_truncate_table`, `arcgis_pro_gp_repair_geometry`, `arcgis_pro_gp_check_geometry`, `arcgis_pro_gp_validate_topology`
- Tables/fields: `arcgis_pro_gp_statistics`, `arcgis_pro_gp_frequency`, `arcgis_pro_gp_table_select`, `arcgis_pro_gp_add_field`, `arcgis_pro_gp_delete_field`, `arcgis_pro_gp_alter_field`, `arcgis_pro_gp_calculate_field`, `arcgis_pro_gp_calculate_geometry`
- Conversion/export: `arcgis_pro_gp_import_csv_to_table`, `arcgis_pro_gp_table_to_table`, `arcgis_pro_gp_xy_table_to_point`, `arcgis_pro_gp_json_to_features`, `arcgis_pro_gp_features_to_json`, `arcgis_pro_gp_kml_to_layer`, `arcgis_pro_gp_excel_to_table`, `arcgis_pro_gp_table_to_excel`, `arcgis_pro_gp_feature_class_to_shapefile`, `arcgis_pro_gp_export_features`, `arcgis_pro_gp_export_table`
- Raster: `arcgis_pro_gp_slope`, `arcgis_pro_gp_aspect`, `arcgis_pro_gp_hillshade`, `arcgis_pro_gp_reclassify`, `arcgis_pro_gp_extract_by_mask`, `arcgis_pro_gp_extract_by_attributes`, `arcgis_pro_gp_zonal_statistics_as_table`, `arcgis_pro_gp_kernel_density`, `arcgis_pro_gp_point_density`, `arcgis_pro_gp_idw`, `arcgis_pro_gp_kriging`, `arcgis_pro_gp_topo_to_raster`, `arcgis_pro_gp_raster_to_polygon`, `arcgis_pro_gp_polygon_to_raster`, `arcgis_pro_gp_feature_to_raster`, `arcgis_pro_gp_raster_calculator`, `arcgis_pro_gp_mosaic_to_new_raster`, `arcgis_pro_gp_clip_raster`, `arcgis_pro_gp_resample`, `arcgis_pro_gp_project_raster`, `arcgis_pro_gp_nibble`
- Generic GP: `arcgis_pro_gp_run_tool`, `arcgis_pro_gp_get_messages`, `arcgis_pro_gp_list_toolboxes`, `arcgis_pro_gp_list_tools_in_toolbox`
- Network analysis: `arcgis_pro_na_create_route_layer`, `arcgis_pro_na_add_locations`, `arcgis_pro_na_solve`, `arcgis_pro_na_service_area`, `arcgis_pro_na_od_matrix`
