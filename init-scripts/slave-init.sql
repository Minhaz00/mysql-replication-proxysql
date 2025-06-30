-- Create monitor user for ProxySQL
CREATE USER IF NOT EXISTS 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT USAGE, REPLICATION CLIENT ON *.* TO 'monitor'@'%';

-- Grant SELECT privileges to existing appuser (created by Docker entrypoint)
GRANT SELECT ON testdb.* TO 'appuser'@'%';

FLUSH PRIVILEGES;