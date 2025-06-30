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