# Complete ProxySQL + FastAPI CRUD Demo Guide

## Table of Contents
1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Project Structure](#project-structure)
4. [Step 1: Create FastAPI Application](#step-1-create-fastapi-application)
5. [Step 2: Create Docker Configuration](#step-2-create-docker-configuration)
6. [Step 3: Create ProxySQL Configuration](#step-3-create-proxysql-configuration)
7. [Step 4: Create Setup Scripts](#step-4-create-setup-scripts)
8. [Step 5: Deploy and Configure](#step-5-deploy-and-configure)
9. [Step 6: Test the Demo](#step-6-test-the-demo)
10. [Step 7: Monitor and Verify](#step-7-monitor-and-verify)
11. [Troubleshooting](#troubleshooting)
12. [Advanced Usage](#advanced-usage)

## Overview

This guide demonstrates how ProxySQL works with MySQL replication using a FastAPI application with CRUD operations. The setup includes:

- **MySQL Master/Source** (writes)
- **2 MySQL Replica servers** (reads)
- **ProxySQL** for connection pooling and read/write splitting
- **FastAPI application** with CRUD operations
- **Automated setup and monitoring**

### Architecture Diagram

```
┌─────────────────┐
│   FastAPI App   │ (Port 5000)
│   (CRUD API)    │
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│    ProxySQL     │ (Port 6033 - MySQL, 6032 - Admin)
│  (Load Balancer)│
└─────────┬───────┘
          │
    ┌─────┴─────┐
    ▼           ▼
┌─────────┐   ┌─────────────────┐
│ Master  │   │    Replicas     │
│(Writes) │   │    (Reads)      │
│Port 3306│   │ Port 3307, 3308 │
└─────────┘   └─────────────────┘
```

## Prerequisites

- **Docker** and **Docker Compose** installed
- **At least 4GB RAM** available for containers
- **Basic knowledge** of MySQL, Docker, and REST APIs
- **curl** or similar tool for testing APIs

### Verify Prerequisites

```bash
# Check Docker
docker --version
docker-compose --version

# Check available resources
docker system df
```

## Project Structure

Create the following directory structure:

```
proxysql-fastapi-demo/
├── main.py                 # FastAPI application
├── requirements.txt        # Python dependencies
├── Dockerfile             # FastAPI container
├── docker-compose.yml     # All services
├── proxysql.cnf           # ProxySQL configuration
├── setup.sh               # Initial setup script
├── fix_auth.sh            # Authentication fix script
├── troubleshoot.sh        # Troubleshooting script
└── README.md              # This documentation
```

## Step 1: Create FastAPI Application

### 1.1 Create the main application file

Create `main.py`:

```python
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional
import pymysql
from contextlib import contextmanager
import logging
import os
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ProxySQL CRUD Demo",
    description="A simple CRUD application demonstrating ProxySQL with MySQL replication",
    version="1.0.0"
)

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 6033)),  # ProxySQL default port
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'mypass'),
    'database': os.getenv('DB_NAME', 'testdb'),
    'charset': 'utf8mb4',
    'autocommit': True
}

# Pydantic models
class UserBase(BaseModel):
    name: str
    email: str
    age: Optional[int] = None

class UserCreate(UserBase):
    pass

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    age: Optional[int] = None

class User(UserBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# Database connection manager
@contextmanager
def get_db_connection():
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)
        logger.info(f"Connected to database via ProxySQL at {DB_CONFIG['host']}:{DB_CONFIG['port']}")
        yield connection
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise HTTPException(status_code=500, detail="Database connection failed")
    finally:
        if connection:
            connection.close()

# Initialize database
def init_database():
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Create database if not exists
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']}")
        cursor.execute(f"USE {DB_CONFIG['database']}")
        
        # Create users table
        create_table_query = """
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(150) UNIQUE NOT NULL,
            age INT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
        cursor.execute(create_table_query)
        conn.commit()
        logger.info("Database and table initialized successfully")

# Startup event
@app.on_event("startup")
async def startup_event():
    init_database()

# Health check endpoint
@app.get("/")
async def root():
    return {"message": "ProxySQL CRUD Demo API", "status": "running"}

@app.get("/health")
async def health_check():
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
            
            # Get ProxySQL connection info
            cursor.execute("SELECT CONNECTION_ID(), USER(), @@hostname")
            conn_info = cursor.fetchone()
            
            return {
                "status": "healthy",
                "database": "connected",
                "connection_id": conn_info[0],
                "user": conn_info[1],
                "server": conn_info[2],
                "proxysql_host": f"{DB_CONFIG['host']}:{DB_CONFIG['port']}"
            }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )

# CRUD Operations

@app.post("/users/", response_model=User)
async def create_user(user: UserCreate):
    """Create a new user (Write operation - goes to master)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # This INSERT will be routed to the master/source server
            insert_query = """
            INSERT INTO users (name, email, age) 
            VALUES (%s, %s, %s)
            """
            cursor.execute(insert_query, (user.name, user.email, user.age))
            conn.commit()
            
            user_id = cursor.lastrowid
            logger.info(f"Created user with ID: {user_id} (routed to master)")
            
            # Fetch the created user
            cursor.execute("""
                SELECT id, name, email, age, created_at, updated_at 
                FROM users WHERE id = %s
            """, (user_id,))
            
            result = cursor.fetchone()
            if result:
                return User(
                    id=result[0],
                    name=result[1],
                    email=result[2],
                    age=result[3],
                    created_at=result[4],
                    updated_at=result[5]
                )
            
        except pymysql.IntegrityError as e:
            if "Duplicate entry" in str(e):
                raise HTTPException(status_code=400, detail="Email already exists")
            raise HTTPException(status_code=400, detail="Database integrity error")
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            raise HTTPException(status_code=500, detail="Failed to create user")

@app.get("/users/", response_model=List[User])
async def get_users(skip: int = 0, limit: int = 10):
    """Get all users (Read operation - can go to replicas)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # This SELECT will be routed to replica servers
            query = """
            SELECT id, name, email, age, created_at, updated_at 
            FROM users 
            ORDER BY created_at DESC 
            LIMIT %s OFFSET %s
            """
            cursor.execute(query, (limit, skip))
            results = cursor.fetchall()
            
            # Get connection info to show which server handled the request
            cursor.execute("SELECT @@hostname, CONNECTION_ID()")
            server_info = cursor.fetchone()
            logger.info(f"Read operation handled by server: {server_info[0]} (connection: {server_info[1]})")
            
            users = []
            for row in results:
                users.append(User(
                    id=row[0],
                    name=row[1],
                    email=row[2],
                    age=row[3],
                    created_at=row[4],
                    updated_at=row[5]
                ))
            
            return users
            
        except Exception as e:
            logger.error(f"Error fetching users: {e}")
            raise HTTPException(status_code=500, detail="Failed to fetch users")

@app.get("/users/{user_id}", response_model=User)
async def get_user(user_id: int):
    """Get a specific user (Read operation - can go to replicas)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # This SELECT will be routed to replica servers
            cursor.execute("""
                SELECT id, name, email, age, created_at, updated_at 
                FROM users WHERE id = %s
            """, (user_id,))
            
            result = cursor.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="User not found")
            
            # Get connection info
            cursor.execute("SELECT @@hostname")
            server_info = cursor.fetchone()
            logger.info(f"Read operation for user {user_id} handled by server: {server_info[0]}")
            
            return User(
                id=result[0],
                name=result[1],
                email=result[2],
                age=result[3],
                created_at=result[4],
                updated_at=result[5]
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error fetching user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to fetch user")

@app.put("/users/{user_id}", response_model=User)
async def update_user(user_id: int, user_update: UserUpdate):
    """Update a user (Write operation - goes to master)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # Check if user exists first
            cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="User not found")
            
            # Build dynamic update query
            update_fields = []
            values = []
            
            if user_update.name is not None:
                update_fields.append("name = %s")
                values.append(user_update.name)
            
            if user_update.email is not None:
                update_fields.append("email = %s")
                values.append(user_update.email)
            
            if user_update.age is not None:
                update_fields.append("age = %s")
                values.append(user_update.age)
            
            if not update_fields:
                raise HTTPException(status_code=400, detail="No fields to update")
            
            # This UPDATE will be routed to the master/source server
            update_query = f"""
            UPDATE users 
            SET {', '.join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """
            values.append(user_id)
            
            cursor.execute(update_query, values)
            conn.commit()
            
            logger.info(f"Updated user {user_id} (routed to master)")
            
            # Fetch updated user
            cursor.execute("""
                SELECT id, name, email, age, created_at, updated_at 
                FROM users WHERE id = %s
            """, (user_id,))
            
            result = cursor.fetchone()
            return User(
                id=result[0],
                name=result[1],
                email=result[2],
                age=result[3],
                created_at=result[4],
                updated_at=result[5]
            )
            
        except HTTPException:
            raise
        except pymysql.IntegrityError as e:
            if "Duplicate entry" in str(e):
                raise HTTPException(status_code=400, detail="Email already exists")
            raise HTTPException(status_code=400, detail="Database integrity error")
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to update user")

@app.delete("/users/{user_id}")
async def delete_user(user_id: int):
    """Delete a user (Write operation - goes to master)"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            # Check if user exists first
            cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="User not found")
            
            # This DELETE will be routed to the master/source server
            cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            conn.commit()
            
            logger.info(f"Deleted user {user_id} (routed to master)")
            
            return {"message": f"User {user_id} deleted successfully"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting user {user_id}: {e}")
            raise HTTPException(status_code=500, detail="Failed to delete user")

# ProxySQL specific endpoints for monitoring

@app.get("/proxysql/stats")
async def get_proxysql_stats():
    """Get ProxySQL statistics and server information"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        try:
            stats = {}
            
            # Get current connection info
            cursor.execute("SELECT CONNECTION_ID(), USER(), @@hostname, @@port")
            conn_info = cursor.fetchone()
            stats['current_connection'] = {
                'connection_id': conn_info[0],
                'user': conn_info[1],
                'hostname': conn_info[2],
                'port': conn_info[3]
            }
            
            # Get server variables that might indicate routing
            cursor.execute("SHOW VARIABLES LIKE 'server_id'")
            server_id_result = cursor.fetchone()
            if server_id_result:
                stats['server_id'] = server_id_result[1]
            
            cursor.execute("SHOW VARIABLES LIKE 'read_only'")
            read_only_result = cursor.fetchone()
            if read_only_result:
                stats['read_only'] = read_only_result[1]
                stats['server_role'] = 'replica' if read_only_result[1] == 'ON' else 'master'
            
            return stats
            
        except Exception as e:
            logger.error(f"Error getting ProxySQL stats: {e}")
            raise HTTPException(status_code=500, detail="Failed to get ProxySQL stats")

# Sample data endpoint for testing
@app.post("/users/sample")
async def create_sample_users():
    """Create sample users for testing"""
    sample_users = [
        {"name": "Alice Johnson", "email": "alice@example.com", "age": 28},
        {"name": "Bob Smith", "email": "bob@example.com", "age": 35},
        {"name": "Carol Davis", "email": "carol@example.com", "age": 31},
        {"name": "David Wilson", "email": "david@example.com", "age": 42},
        {"name": "Eve Brown", "email": "eve@example.com", "age": 29}
    ]
    
    created_users = []
    for user_data in sample_users:
        try:
            user = UserCreate(**user_data)
            created_user = await create_user(user)
            created_users.append(created_user)
        except HTTPException as e:
            if "already exists" in str(e.detail):
                continue  # Skip if user already exists
            raise
    
    return {"message": f"Created {len(created_users)} sample users", "users": created_users}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('PORT', 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

### 1.2 Create Python dependencies file

Create `requirements.txt`:

```txt
fastapi==0.104.1
uvicorn[standard]==0.24.0
pymysql==1.1.0
pydantic==2.5.0
python-multipart==0.0.6
```

## Step 2: Create Docker Configuration

### 2.1 Create Dockerfile for FastAPI

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Expose port
EXPOSE 5000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000", "--reload"]
```

### 2.2 Create Docker Compose configuration

Create `docker-compose.yml`:

```yaml
version: '3.8'

networks:
  replicanet:
    driver: bridge

services:
  # MySQL Master/Source Server
  mysql-source:
    image: mysql/mysql-server:5.7
    container_name: mysql-source
    hostname: source
    networks:
      - replicanet
    environment:
      MYSQL_ROOT_PASSWORD: mypass
      MYSQL_DATABASE: testdb
    command: >
      --server-id=10
      --log-bin=mysql-bin-1.log
      --relay_log_info_repository=TABLE
      --master-info-repository=TABLE
      --gtid-mode=ON
      --log-slave-updates=ON
      --enforce-gtid-consistency
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-u", "root", "-pmypass"]
      timeout: 20s
      retries: 10
    ports:
      - "3306:3306"

  # MySQL Replica 1
  mysql-replica1:
    image: mysql/mysql-server:5.7
    container_name: mysql-replica1
    hostname: replica1
    networks:
      - replicanet
    environment:
      MYSQL_ROOT_PASSWORD: mypass
    command: >
      --server-id=20
      --log-bin=mysql-bin-1.log
      --relay_log_info_repository=TABLE
      --master-info-repository=TABLE
      --gtid-mode=ON
      --log-slave-updates=ON
      --enforce-gtid-consistency
      --read-only
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-u", "root", "-pmypass"]
      timeout: 20s
      retries: 10
    ports:
      - "3307:3306"
    depends_on:
      - mysql-source

  # MySQL Replica 2
  mysql-replica2:
    image: mysql/mysql-server:5.7
    container_name: mysql-replica2
    hostname: replica2
    networks:
      - replicanet
    environment:
      MYSQL_ROOT_PASSWORD: mypass
    command: >
      --server-id=30
      --log-bin=mysql-bin-1.log
      --relay_log_info_repository=TABLE
      --master-info-repository=TABLE
      --gtid-mode=ON
      --log-slave-updates=ON
      --enforce-gtid-consistency
      --read-only
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-u", "root", "-pmypass"]
      timeout: 20s
      retries: 10
    ports:
      - "3308:3306"
    depends_on:
      - mysql-source

  # ProxySQL
  proxysql:
    image: proxysql/proxysql:2.5.5
    container_name: proxysql
    hostname: proxysql
    networks:
      - replicanet
    volumes:
      - ./proxysql.cnf:/etc/proxysql.cnf
    healthcheck:
      test: ["CMD", "proxysql_admin", "-u", "admin", "-padmin", "-h", "127.0.0.1", "-P", "6032", "--execute", "SELECT 1"]
      timeout: 20s
      retries: 5
    ports:
      - "6032:6032"  # Admin interface
      - "6033:6033"  # MySQL interface
    depends_on:
      - mysql-source
      - mysql-replica1
      - mysql-replica2

  # FastAPI Application
  fastapi-app:
    build: .
    container_name: fastapi-crud-app
    networks:
      - replicanet
    environment:
      DB_HOST: proxysql
      DB_PORT: 6033
      DB_USER: root
      DB_PASSWORD: mypass
      DB_NAME: testdb
      PORT: 5000
    ports:
      - "5000:5000"
    depends_on:
      - proxysql
    volumes:
      - .:/app
    restart: unless-stopped
```

## Step 3: Create ProxySQL Configuration

Create `proxysql.cnf`:

```ini
datadir="/var/lib/proxysql"
errorlog="/var/lib/proxysql/proxysql.log"

admin_variables=
{
    admin_credentials="admin:admin;radmin:radmin"
    mysql_ifaces="0.0.0.0:6032"
    refresh_interval=2000
    web_enabled=true
    web_port=6080
    restapi_enabled=true
    restapi_port=6070
}

mysql_variables=
{
    threads=4
    max_connections=2048
    default_query_delay=0
    default_query_timeout=36000000
    have_compress=true
    poll_timeout=2000
    interfaces="0.0.0.0:6033"
    default_schema="information_schema"
    stacksize=1048576
    server_version="5.7.22"
    connect_timeout_server=3000
    monitor_username="monitor"
    monitor_password="monitor"
    monitor_history=600000
    monitor_connect_interval=60000
    monitor_ping_interval=10000
    monitor_read_only_interval=1500
    monitor_read_only_timeout=500
    ping_interval_server_msec=120000
    ping_timeout_server=500
    commands_stats=true
    sessions_sort=true
    connect_retries_on_failure=10
}

# MySQL Servers
mysql_servers =
(
    {
        address="source"
        port=3306
        hostgroup=10
        max_connections=100
        max_replication_lag=5
        weight=1
        comment="Master/Source Server"
    },
    {
        address="replica1"
        port=3306
        hostgroup=20
        max_connections=100
        max_replication_lag=5
        weight=1
        comment="Replica Server 1"
    },
    {
        address="replica2"
        port=3306
        hostgroup=20
        max_connections=100
        max_replication_lag=5
        weight=1
        comment="Replica Server 2"
    }
)

# MySQL Users
mysql_users =
(
    {
        username="root"
        password="mypass"
        default_hostgroup=10
        transaction_persistent=1
        comment="Root user"
    },
    {
        username="monitor"
        password="monitor"
        default_hostgroup=10
        active=1
        comment="Monitor user"
    }
)

# MySQL Query Rules for Read/Write Splitting
mysql_query_rules =
(
    {
        rule_id=1
        active=1
        match_pattern="^SELECT.*"
        destination_hostgroup=20
        apply=1
        comment="Route SELECT queries to replica servers"
    },
    {
        rule_id=2
        active=1
        match_pattern="^INSERT.*"
        destination_hostgroup=10
        apply=1
        comment="Route INSERT queries to master server"
    },
    {
        rule_id=3
        active=1
        match_pattern="^UPDATE.*"
        destination_hostgroup=10
        apply=1
        comment="Route UPDATE queries to master server"
    },
    {
        rule_id=4
        active=1
        match_pattern="^DELETE.*"
        destination_hostgroup=10
        apply=1
        comment="Route DELETE queries to master server"
    },
    {
        rule_id=5
        active=1
        match_pattern="^CREATE.*"
        destination_hostgroup=10
        apply=1
        comment="Route CREATE queries to master server"
    },
    {
        rule_id=6
        active=1
        match_pattern="^ALTER.*"
        destination_hostgroup=10
        apply=1
        comment="Route ALTER queries to master server"
    },
    {
        rule_id=7
        active=1
        match_pattern="^DROP.*"
        destination_hostgroup=10
        apply=1
        comment="Route DROP queries to master server"
    }
)

# MySQL Replication Hostgroups
mysql_replication_hostgroups =
(
    {
        writer_hostgroup=10
        reader_hostgroup=20
        comment="MySQL Replication Setup"
    }
)
```

## Step 4: Create Setup Scripts

### 4.1 Create main setup script

Create `setup.sh`:

```bash
#!/bin/bash

echo "=== Setting up ProxySQL with MySQL Replication and FastAPI CRUD Demo ==="

# Create network if it doesn't exist
docker network create replicanet 2>/dev/null || true

# Start the services
echo "Starting MySQL Master and Replicas..."
docker-compose up -d mysql-source mysql-replica1 mysql-replica2

# Wait for MySQL servers to be ready
echo "Waiting for MySQL servers to be ready..."
sleep 30

# Configure replication
echo "Configuring MySQL replication..."

# Create replication user on master
docker exec mysql-source mysql -u root -pmypass -e "
CREATE USER IF NOT EXISTS 'repl'@'%' IDENTIFIED BY 'replpass';
GRANT REPLICATION SLAVE ON *.* TO 'repl'@'%';
CREATE USER IF NOT EXISTS 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT USAGE, REPLICATION CLIENT ON *.* TO 'monitor'@'%';
FLUSH PRIVILEGES;
"

# Get master status
MASTER_STATUS=$(docker exec mysql-source mysql -u root -pmypass -e "SHOW MASTER STATUS\G")
echo "Master Status: $MASTER_STATUS"

# Configure replica1
echo "Configuring replica1..."
docker exec mysql-replica1 mysql -u root -pmypass -e "
CHANGE MASTER TO 
MASTER_HOST='source',
MASTER_USER='repl',
MASTER_PASSWORD='replpass',
MASTER_AUTO_POSITION=1;
START SLAVE;
"

# Configure replica2
echo "Configuring replica2..."
docker exec mysql-replica2 mysql -u root -pmypass -e "
CHANGE MASTER TO 
MASTER_HOST='source',
MASTER_USER='repl',
MASTER_PASSWORD='replpass',
MASTER_AUTO_POSITION=1;
START SLAVE;
"

# Wait a bit for replication to start
sleep 10

# Check replication status
echo "Checking replication status..."
docker exec mysql-replica1 mysql -u root -pmypass -e "SHOW SLAVE STATUS\G" | grep -E "(Slave_IO_Running|Slave_SQL_Running)"
docker exec mysql-replica2 mysql -u root -pmypass -e "SHOW SLAVE STATUS\G" | grep -E "(Slave_IO_Running|Slave_SQL_Running)"

# Start ProxySQL
echo "Starting ProxySQL..."
docker-compose up -d proxysql

# Wait for ProxySQL to be ready
echo "Waiting for ProxySQL to be ready..."
sleep 15

# Start FastAPI application
echo "Starting FastAPI application..."
docker-compose up -d fastapi-app

echo "Setup complete!"
echo ""
echo "Services available at:"
echo "- FastAPI Application: http://localhost:5000"
echo "- FastAPI Docs: http://localhost:5000/docs"
echo "- ProxySQL Admin: mysql -h 127.0.0.1 -P 6032 -u admin -padmin"
echo "- ProxySQL MySQL Interface: mysql -h 127.0.0.1 -P 6033 -u root -pmypass"
echo ""
echo "To test the setup:"
echo "1. Visit http://localhost:5000/docs for interactive API documentation"
echo "2. Use the /users/sample endpoint to create sample data"
echo "3. Use the /proxysql/stats endpoint to see which server handled requests"
echo "4. Monitor ProxySQL: docker exec -it proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032"
echo ""
echo "Useful ProxySQL monitoring commands:"
echo "SELECT * FROM mysql_servers;"
echo "SELECT * FROM stats_mysql_connection_pool;"
echo "SELECT * FROM stats_mysql_query_rules;"
echo "SELECT * FROM monitor.mysql_server_connect_log ORDER BY time_start_us DESC LIMIT 10;"

### 4.2 Create authentication fix script

Create `fix_auth.sh`:

```bash
#!/bin/bash

echo "=== Fixing MySQL Authentication for ProxySQL ==="

# Fix authentication on master server
echo "1. Fixing authentication on master server..."
docker exec mysql-source mysql -u root -pmypass -e "
UPDATE mysql.user SET host='%' WHERE user='root' AND host='localhost';
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'mypass';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT USAGE, REPLICATION CLIENT ON *.* TO 'monitor'@'%';
FLUSH PRIVILEGES;
"

echo "2. Fixing authentication on replica1..."
docker exec mysql-replica1 mysql -u root -pmypass -e "
UPDATE mysql.user SET host='%' WHERE user='root' AND host='localhost';
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'mypass';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT USAGE, REPLICATION CLIENT ON *.* TO 'monitor'@'%';
FLUSH PRIVILEGES;
"

echo "3. Fixing authentication on replica2..."
docker exec mysql-replica2 mysql -u root -pmypass -e "
UPDATE mysql.user SET host='%' WHERE user='root' AND host='localhost';
CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED BY 'mypass';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS 'monitor'@'%' IDENTIFIED BY 'monitor';
GRANT USAGE, REPLICATION CLIENT ON *.* TO 'monitor'@'%';
FLUSH PRIVILEGES;
"

echo "4. Restarting ProxySQL to clear connection errors..."
docker-compose restart proxysql

echo "5. Waiting for ProxySQL to reconnect..."
sleep 15

echo "6. Testing ProxySQL connectivity..."
# Test admin interface
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "SELECT * FROM mysql_servers;" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ ProxySQL admin interface is working"
else
    echo "✗ ProxySQL admin interface still not working"
fi

# Test MySQL interface
docker exec proxysql mysql -u root -pmypass -h 127.0.0.1 -P 6033 -e "SELECT 'ProxySQL MySQL interface works', @@hostname;" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ ProxySQL MySQL interface is working"
else
    echo "✗ ProxySQL MySQL interface still not working"
fi

echo "7. Restarting FastAPI application..."
docker-compose restart fastapi-app

echo "8. Waiting for FastAPI to start..."
sleep 10

echo "9. Testing FastAPI application..."
response=$(curl -s -w "%{http_code}" http://localhost:5000 -o /dev/null 2>/dev/null)
if [ "$response" = "200" ]; then
    echo "✓ FastAPI is now responding on port 5000"
    curl -s http://localhost:5000
else
    echo "✗ FastAPI still not responding (HTTP $response)"
    echo "Checking FastAPI logs..."
    docker-compose logs --tail=5 fastapi-app
fi

echo ""
echo "=== Authentication fix complete ==="
echo "Try: curl http://localhost:5000"
```

### 4.3 Create troubleshooting script

Create `troubleshoot.sh`:

```bash
#!/bin/bash

echo "=== ProxySQL FastAPI Troubleshooting ==="

echo "1. Checking container status..."
docker-compose ps

echo ""
echo "2. Checking ProxySQL health..."
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "SELECT 1" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ ProxySQL admin interface is accessible"
else
    echo "✗ ProxySQL admin interface is not accessible"
    echo "Checking ProxySQL logs..."
    docker-compose logs --tail=20 proxysql
fi

echo ""
echo "3. Checking MySQL replication status..."
echo "Master status:"
docker exec mysql-source mysql -u root -pmypass -e "SHOW MASTER STATUS" 2>/dev/null

echo ""
echo "Replica1 status:"
docker exec mysql-replica1 mysql -u root -pmypass -e "SHOW SLAVE STATUS\G" 2>/dev/null | grep -E "(Slave_IO_Running|Slave_SQL_Running|Seconds_Behind_Master)"

echo ""
echo "Replica2 status:"
docker exec mysql-replica2 mysql -u root -pmypass -e "SHOW SLAVE STATUS\G" 2>/dev/null | grep -E "(Slave_IO_Running|Slave_SQL_Running|Seconds_Behind_Master)"

echo ""
echo "4. Testing database connectivity through ProxySQL..."
docker exec proxysql mysql -u root -pmypass -h 127.0.0.1 -P 6033 -e "SELECT 'ProxySQL connection works', @@hostname" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "✓ Can connect to MySQL through ProxySQL"
else
    echo "✗ Cannot connect to MySQL through ProxySQL"
fi

echo ""
echo "5. Checking FastAPI app logs..."
docker-compose logs --tail=10 fastapi-app

echo ""
echo "6. Testing FastAPI connectivity..."
response=$(curl -s -w "%{http_code}" http://localhost:5000 -o /dev/null 2>/dev/null)
if [ "$response" = "200" ]; then
    echo "✓ FastAPI is responding on port 5000"
    curl -s http://localhost:5000 | head -20
else
    echo "✗ FastAPI is not responding properly (HTTP $response)"
    echo "Attempting to restart FastAPI container..."
    docker-compose restart fastapi-app
    sleep 10
    echo "Retesting after restart..."
    curl -s http://localhost:5000 | head -20
fi

echo ""
echo "7. ProxySQL server status (if accessible)..."
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "SELECT hostgroup_id, hostname, port, status FROM mysql_servers" 2>/dev/null

echo ""
echo "=== Troubleshooting complete ==="
```

## Step 5: Deploy and Configure

### 5.1 Set up the project directory

```bash
# Create project directory
mkdir proxysql-fastapi-demo
cd proxysql-fastapi-demo

# Create all the files from Steps 1-4 above
# (Copy the content from each section into the respective files)
```

### 5.2 Make scripts executable

```bash
chmod +x setup.sh
chmod +x fix_auth.sh
chmod +x troubleshoot.sh
```

### 5.3 Run the initial setup

```bash
# Run the complete setup
./setup.sh
```

**Expected Output:**
```
=== Setting up ProxySQL with MySQL Replication and FastAPI CRUD Demo ===
Starting MySQL Master and Replicas...
Waiting for MySQL servers to be ready...
Configuring MySQL replication...
Master Status: ...
Configuring replica1...
Configuring replica2...
Checking replication status...
Slave_IO_Running: Yes
Slave_SQL_Running: Yes
Starting ProxySQL...
Waiting for ProxySQL to be ready...
Starting FastAPI application...
Setup complete!
```

### 5.4 Fix authentication issues (if needed)

If you encounter authentication errors:

```bash
# Run the authentication fix
./fix_auth.sh
```

### 5.5 Verify all services are running

```bash
# Check container status
docker-compose ps

# All services should show "Up" status
# ProxySQL might show "Up (healthy)" or "Up (unhealthy)"
```

## Step 6: Test the Demo

### 6.1 Basic connectivity tests

```bash
# Test FastAPI basic endpoint
curl http://localhost:5000

# Expected response:
# {"message":"ProxySQL CRUD Demo API","status":"running"}

# Test health check
curl http://localhost:5000/health

# Expected response includes database connection info
```

### 6.2 Test CRUD operations

#### Create Sample Users

```bash
# Create sample users (writes to master)
curl -X POST "http://localhost:5000/users/sample"
```

#### Read Operations (routed to replicas)

```bash
# Get all users
curl "http://localhost:5000/users/"

# Get specific user
curl "http://localhost:5000/users/1"
```

#### Create Individual User (write to master)

```bash
curl -X POST "http://localhost:5000/users/" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Doe",
    "email": "john@example.com",
    "age": 30
  }'
```

#### Update User (write to master)

```bash
curl -X PUT "http://localhost:5000/users/1" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "John Smith",
    "age": 31
  }'
```

#### Delete User (write to master)

```bash
curl -X DELETE "http://localhost:5000/users/1"
```

### 6.3 Interactive API Documentation

Open your browser and visit:
- **API Documentation**: http://localhost:5000/docs
- **Alternative docs**: http://localhost:5000/redoc

## Step 7: Monitor and Verify

### 7.1 Monitor FastAPI logs

```bash
# Watch FastAPI logs to see routing in action
docker-compose logs -f fastapi-app
```

**Expected log entries:**
```
INFO:main:Created user with ID: 1 (routed to master)
INFO:main:Read operation handled by server: replica1 (connection: 200)
INFO:main:Read operation handled by server: replica2 (connection: 203)
```

### 7.2 Monitor ProxySQL statistics

#### Connection Pool Stats

```bash
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT hostgroup_id, srv_host, srv_port, status, Queries 
FROM stats_mysql_connection_pool 
ORDER BY hostgroup_id, srv_host;"
```

**Expected output:**
```
hostgroup_id | srv_host | srv_port | status | Queries
10           | source   | 3306     | ONLINE | 15
20           | replica1 | 3306     | ONLINE | 8
20           | replica2 | 3306     | ONLINE | 12
```

#### Query Rules Statistics

```bash
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT rule_id, hits, match_pattern, destination_hostgroup 
FROM stats_mysql_query_rules 
ORDER BY hits DESC;"
```

#### Server Status

```bash
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT hostgroup_id, hostname, port, status, weight 
FROM mysql_servers 
ORDER BY hostgroup_id;"
```

### 7.3 Verify read/write splitting

#### Method 1: API Stats Endpoint

```bash
# Check which server handled the request
curl "http://localhost:5000/proxysql/stats"
```

#### Method 2: Load Testing

```bash
# Run multiple read operations
for i in {1..10}; do
  curl -s "http://localhost:5000/users/" > /dev/null
done

# Run multiple write operations
for i in {1..5}; do
  curl -s -X POST "http://localhost:5000/users/" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"User $i\", \"email\": \"user$i@test.com\", \"age\": $((20+i))}" > /dev/null
done

# Check updated stats
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT hostgroup_id, srv_host, Queries 
FROM stats_mysql_connection_pool 
ORDER BY hostgroup_id, srv_host;"
```

### 7.4 Verify MySQL replication

```bash
# Check replication status
docker exec mysql-replica1 mysql -u root -pmypass -e "SHOW SLAVE STATUS\G" | grep -E "(Slave_IO_Running|Slave_SQL_Running|Seconds_Behind_Master)"

docker exec mysql-replica2 mysql -u root -pmypass -e "SHOW SLAVE STATUS\G" | grep -E "(Slave_IO_Running|Slave_SQL_Running|Seconds_Behind_Master)"
```

**Expected output:**
```
Slave_IO_Running: Yes
Slave_SQL_Running: Yes
Seconds_Behind_Master: 0
```

## Troubleshooting

### Common Issues and Solutions

#### 1. FastAPI fails to start with database connection errors

**Symptoms:**
```
ERROR:main:Database connection error: (1045, "Access denied for user 'root'@'172.27.0.5'")
```

**Solution:**
```bash
./fix_auth.sh
```

#### 2. ProxySQL shows as unhealthy

**Symptoms:**
```
proxysql    Up 5 minutes (unhealthy)
```

**Solution:**
```bash
# Check ProxySQL logs
docker-compose logs proxysql

# Restart ProxySQL
docker-compose restart proxysql

# Wait and check again
sleep 15
docker-compose ps
```

#### 3. MySQL replication not working

**Symptoms:**
```
Slave_IO_Running: No
Slave_SQL_Running: No
```

**Solution:**
```bash
# Reset replication
docker exec mysql-replica1 mysql -u root -pmypass -e "
STOP SLAVE;
RESET SLAVE;
CHANGE MASTER TO 
MASTER_HOST='source',
MASTER_USER='repl',
MASTER_PASSWORD='replpass',
MASTER_AUTO_POSITION=1;
START SLAVE;
"
```

#### 4. No read/write splitting observed

**Symptoms:**
All queries going to master only.

**Solution:**
```bash
# Check query rules
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT rule_id, active, match_pattern, destination_hostgroup 
FROM mysql_query_rules;"

# Load rules to runtime if needed
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
LOAD MYSQL QUERY RULES TO RUNTIME;
SAVE MYSQL QUERY RULES TO DISK;
"
```

#### 5. Complete reset

If everything fails:

```bash
# Stop and remove all containers
docker-compose down -v

# Remove network
docker network rm replicanet

# Start fresh
./setup.sh
./fix_auth.sh
```

### Debugging Commands

```bash
# Check all container logs
docker-compose logs

# Check specific service logs
docker-compose logs mysql-source
docker-compose logs proxysql
docker-compose logs fastapi-app

# Check container resource usage
docker stats

# Enter ProxySQL admin interface
docker exec -it proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032

# Connect directly to MySQL servers
docker exec -it mysql-source mysql -u root -pmypass
docker exec -it mysql-replica1 mysql -u root -pmypass
```

## Advanced Usage

### Performance Testing

#### Using Apache Bench

```bash
# Install apache bench (if not available)
sudo apt-get install apache2-utils  # Ubuntu/Debian
# brew install apache2-utils        # macOS

# Test read operations (should distribute across replicas)
ab -n 100 -c 10 http://localhost:5000/users/

# Create JSON file for write tests
echo '{"name": "Test User", "email": "test@example.com", "age": 25}' > user_data.json

# Test write operations (should all go to master)
ab -n 50 -c 5 -p user_data.json -T application/json http://localhost:5000/users/
```

#### Using Python script

Create `load_test.py`:

```python
import asyncio
import aiohttp
import time
import json

async def read_test(session, url):
    async with session.get(f"{url}/users/") as response:
        return await response.json()

async def write_test(session, url, user_data):
    async with session.post(f"{url}/users/", json=user_data) as response:
        return await response.json()

async def main():
    url = "http://localhost:5000"
    
    async with aiohttp.ClientSession() as session:
        # Test reads
        print("Testing read operations...")
        start_time = time.time()
        read_tasks = [read_test(session, url) for _ in range(50)]
        await asyncio.gather(*read_tasks)
        read_time = time.time() - start_time
        print(f"50 read operations completed in {read_time:.2f} seconds")
        
        # Test writes
        print("Testing write operations...")
        start_time = time.time()
        user_data = {"name": "Load Test User", "email": f"loadtest{int(time.time())}@example.com", "age": 25}
        write_tasks = [write_test(session, url, {**user_data, "email": f"user{i}@test.com"}) for i in range(20)]
        await asyncio.gather(*write_tasks)
        write_time = time.time() - start_time
        print(f"20 write operations completed in {write_time:.2f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
```

Run the load test:

```bash
pip install aiohttp
python load_test.py
```

### Monitoring Dashboard

#### ProxySQL Web Interface

If enabled in the configuration:
- **Web Interface**: http://localhost:6080/

#### Custom monitoring queries

```bash
# Monitor query digest (most executed queries)
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT digest_text, count_star, sum_time, avg_time 
FROM stats_mysql_query_digest 
ORDER BY count_star DESC 
LIMIT 10;"

# Monitor connection history
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT * FROM monitor.mysql_server_connect_log 
ORDER BY time_start_us DESC 
LIMIT 10;"

# Monitor read-only status changes
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT * FROM monitor.mysql_server_read_only_log 
ORDER BY time_start_us DESC 
LIMIT 10;"
```

### Scaling and Production Considerations

#### 1. Security Improvements

```bash
# Change default passwords in proxysql.cnf
admin_credentials="admin:strong_admin_pass;radmin:strong_radmin_pass"

# Use environment variables for MySQL passwords
# Update docker-compose.yml to use secrets
```

#### 2. Connection Pool Tuning

```ini
# In proxysql.cnf, adjust based on your load
mysql_variables=
{
    max_connections=4096
    default_query_timeout=10000000
    connect_timeout_server=1000
    monitor_ping_interval=5000
}
```

#### 3. Adding More Replicas

```yaml
# Add to docker-compose.yml
mysql-replica3:
  image: mysql/mysql-server:5.7
  container_name: mysql-replica3
  hostname: replica3
  # ... same configuration as other replicas
  ports:
    - "3309:3306"
```

```ini
# Add to proxysql.cnf
{
    address="replica3"
    port=3306
    hostgroup=20
    max_connections=100
    max_replication_lag=5
    weight=1
    comment="Replica Server 3"
}
```

#### 4. Health Check Improvements

```bash
# Enhanced health check script
#!/bin/bash
# health_check.sh

# Check all services
for service in mysql-source mysql-replica1 mysql-replica2 proxysql fastapi-app; do
    if docker-compose ps | grep -q "$service.*Up"; then
        echo "✓ $service is running"
    else
        echo "✗ $service is not running"
    fi
done

# Check ProxySQL backend health
docker exec proxysql mysql -u admin -padmin -h 127.0.0.1 -P 6032 -e "
SELECT hostgroup_id, hostname, status, Queries, Bytes_data_sent 
FROM stats_mysql_connection_pool 
WHERE status != 'ONLINE';" 2>/dev/null | grep -v "Empty set" && echo "⚠ Some backends are not online"
```

### Clean Up

#### Complete cleanup

```bash
# Stop all services
docker-compose down

# Remove volumes (this will delete all data)
docker-compose down -v

# Remove the network
docker network rm replicanet

# Remove built images (optional)
docker rmi proxysql-fastapi-demo-fastapi-app

# Remove downloaded images (optional)
docker rmi mysql/mysql-server:5.7 proxysql/proxysql:2.5.5
```

#### Partial cleanup (keep data)

```bash
# Stop services but keep volumes
docker-compose down

# Start again with existing data
docker-compose up -d
```

---

## Conclusion

This complete guide demonstrates a production-ready setup of ProxySQL with MySQL replication and a modern FastAPI application. The setup showcases:

- **Read/Write Splitting**: Automatic routing of queries based on SQL patterns
- **Load Balancing**: Distribution of read queries across multiple replica servers
- **Connection Pooling**: Efficient database connection management
- **Health Monitoring**: Real-time monitoring of database server health
- **Scalability**: Easy addition of more replica servers
- **Observability**: Comprehensive logging and statistics

The demo provides a solid foundation for understanding how ProxySQL improves database performance and can be extended for production use cases.

### Next Steps

1. **Explore ProxySQL Features**: Query caching, connection multiplexing, SSL support
2. **Add Monitoring**: Integrate with Prometheus/Grafana for metrics visualization
3. **Implement Failover**: Test automatic failover scenarios
4. **Performance Tuning**: Optimize configuration for your specific workload
5. **Security Hardening**: Implement proper authentication and network security

For more information, visit:
- [ProxySQL Documentation](https://proxysql.com/documentation/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [MySQL Replication Guide](https://dev.mysql.com/doc/refman/5.7/en/replication.html)