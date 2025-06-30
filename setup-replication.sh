#!/bin/bash

echo "Setting up MySQL Replication..."

# Wait for MySQL master to be ready
echo "Waiting for MySQL master to be ready..."
until docker exec mysql-master mysqladmin ping -h localhost -u root -prootpass --silent; do
    sleep 2
done
echo "MySQL master is ready!"

# Wait for MySQL slaves to be ready
echo "Waiting for MySQL slaves to be ready..."
until docker exec mysql-slave1 mysqladmin ping -h localhost -u root -prootpass --silent; do
    sleep 2
done
echo "MySQL slave1 is ready!"

until docker exec mysql-slave2 mysqladmin ping -h localhost -u root -prootpass --silent; do
    sleep 2
done
echo "MySQL slave2 is ready!"

# Get master status
echo "Getting master status..."
MASTER_STATUS=$(docker exec mysql-master mysql -u root -prootpass -e "SHOW MASTER STATUS\G")
echo "$MASTER_STATUS"

# Configure slave1
echo "Configuring slave1..."
docker exec mysql-slave1 mysql -u root -prootpass -e "
CHANGE MASTER TO 
    MASTER_HOST='mysql-master',
    MASTER_USER='replication',
    MASTER_PASSWORD='replicapass',
    MASTER_AUTO_POSITION=1;
START SLAVE;
"

# Configure slave2
echo "Configuring slave2..."
docker exec mysql-slave2 mysql -u root -prootpass -e "
CHANGE MASTER TO 
    MASTER_HOST='mysql-master',
    MASTER_USER='replication',
    MASTER_PASSWORD='replicapass',
    MASTER_AUTO_POSITION=1;
START SLAVE;
"

# Check slave status
echo "Checking slave1 status..."
docker exec mysql-slave1 mysql -u root -prootpass -e "SHOW SLAVE STATUS\G" | grep -E "Slave_IO_Running|Slave_SQL_Running|Last_Error"

echo "Checking slave2 status..."
docker exec mysql-slave2 mysql -u root -prootpass -e "SHOW SLAVE STATUS\G" | grep -E "Slave_IO_Running|Slave_SQL_Running|Last_Error"

echo "Replication setup complete!"
echo ""
echo "You can now access:"
echo "- FastAPI app: http://localhost:8000"
echo "- ProxySQL admin: mysql -h 127.0.0.1 -P 6032 -u admin -padmin"
echo "- MySQL via ProxySQL: mysql -h 127.0.0.1 -P 6033 -u appuser -papppass testdb"