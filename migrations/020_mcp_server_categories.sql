-- Add a selectable market category to user and platform MCP connections.
SET @column_exists := (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'mcp_servers'
      AND column_name = 'category'
);
SET @sql := IF(
    @column_exists = 0,
    'ALTER TABLE mcp_servers ADD COLUMN category VARCHAR(50) NOT NULL DEFAULT ''general'' AFTER description',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
