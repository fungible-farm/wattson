SELECT asset_id, region, rating_mw, _sheet, _excel_row, _cells, _source_sha256
FROM data
WHERE region = $region
ORDER BY asset_id
