from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import pymysql
import os
from contextlib import contextmanager
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ProxySQL MySQL Demo", version="1.0.0")

# Database configuration
DB_CONFIG = {
    'host': 'proxysql',
    'port': 6033,
    'user': 'appuser',
    'password': 'apppass',
    'database': 'testdb',
    'charset': 'utf8mb4',
    'autocommit': True
}

# Pydantic models
class UserCreate(BaseModel):
    name: str
    email: str

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None

class User(BaseModel):
    id: int
    name: str
    email: str
    created_at: str
    updated_at: str

class HealthCheck(BaseModel):
    status: str
    database: str
    message: str

# Database connection context manager
@contextmanager
def get_db_connection():
    connection = None
    try:
        connection = pymysql.connect(**DB_CONFIG)
        yield connection
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()

# Health check endpoint
@app.get("/health", response_model=HealthCheck)
async def health_check():
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                if result:
                    return HealthCheck(
                        status="healthy",
                        database="connected",
                        message="ProxySQL connection successful"
                    )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(status_code=503, detail=f"Database connection failed: {str(e)}")

# Get all users (READ operation - will go to slaves)
@app.get("/users", response_model=List[User])
async def get_users():
    try:
        with get_db_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT id, name, email, 
                           DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') as created_at,
                           DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at 
                    FROM users ORDER BY id
                """)
                users = cursor.fetchall()
                logger.info(f"Retrieved {len(users)} users from database")
                return users
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")

# Get user by ID (READ operation - will go to slaves)
@app.get("/users/{user_id}", response_model=User)
async def get_user(user_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    SELECT id, name, email, 
                           DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') as created_at,
                           DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at 
                    FROM users WHERE id = %s
                """, (user_id,))
                user = cursor.fetchone()
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")
                logger.info(f"Retrieved user {user_id} from database")
                return user
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching user: {str(e)}")

# Create user (WRITE operation - will go to master)
@app.post("/users", response_model=User)
async def create_user(user: UserCreate):
    try:
        with get_db_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute("""
                    INSERT INTO users (name, email) VALUES (%s, %s)
                """, (user.name, user.email))
                conn.commit()
                
                # Get the created user
                user_id = cursor.lastrowid
                cursor.execute("""
                    SELECT id, name, email, 
                           DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') as created_at,
                           DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at 
                    FROM users WHERE id = %s
                """, (user_id,))
                created_user = cursor.fetchone()
                logger.info(f"Created user {user_id} in database")
                return created_user
    except pymysql.IntegrityError as e:
        logger.error(f"Integrity error creating user: {e}")
        raise HTTPException(status_code=400, detail="Email already exists")
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating user: {str(e)}")

# Update user (WRITE operation - will go to master)
@app.put("/users/{user_id}", response_model=User)
async def update_user(user_id: int, user: UserUpdate):
    try:
        with get_db_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # Check if user exists
                cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=404, detail="User not found")
                
                # Build update query dynamically
                update_fields = []
                values = []
                
                if user.name is not None:
                    update_fields.append("name = %s")
                    values.append(user.name)
                
                if user.email is not None:
                    update_fields.append("email = %s")
                    values.append(user.email)
                
                if not update_fields:
                    raise HTTPException(status_code=400, detail="No fields to update")
                
                values.append(user_id)
                update_query = f"UPDATE users SET {', '.join(update_fields)} WHERE id = %s"
                
                cursor.execute(update_query, values)
                conn.commit()
                
                # Get the updated user
                cursor.execute("""
                    SELECT id, name, email, 
                           DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') as created_at,
                           DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at 
                    FROM users WHERE id = %s
                """, (user_id,))
                updated_user = cursor.fetchone()
                logger.info(f"Updated user {user_id} in database")
                return updated_user
    except HTTPException:
        raise
    except pymysql.IntegrityError as e:
        logger.error(f"Integrity error updating user: {e}")
        raise HTTPException(status_code=400, detail="Email already exists")
    except Exception as e:
        logger.error(f"Error updating user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating user: {str(e)}")

# Delete user (WRITE operation - will go to master)
@app.delete("/users/{user_id}")
async def delete_user(user_id: int):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Check if user exists
                cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=404, detail="User not found")
                
                # Delete user
                cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                conn.commit()
                logger.info(f"Deleted user {user_id} from database")
                return {"message": f"User {user_id} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting user: {str(e)}")

# Search users (READ operation - will go to slaves)
@app.get("/users/search/{query}", response_model=List[User])
async def search_users(query: str):
    try:
        with get_db_connection() as conn:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                search_query = f"%{query}%"
                cursor.execute("""
                    SELECT id, name, email, 
                           DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s') as created_at,
                           DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at 
                    FROM users 
                    WHERE name LIKE %s OR email LIKE %s 
                    ORDER BY id
                """, (search_query, search_query))
                users = cursor.fetchall()
                logger.info(f"Found {len(users)} users matching query: {query}")
                return users
    except Exception as e:
        logger.error(f"Error searching users: {e}")
        raise HTTPException(status_code=500, detail=f"Error searching users: {str(e)}")

# Root endpoint
@app.get("/")
async def root():
    return {
        "message": "ProxySQL MySQL Demo API",
        "version": "1.0.0",
        "endpoints": {
            "health": "/health",
            "users": "/users",
            "create_user": "POST /users",
            "get_user": "/users/{id}",
            "update_user": "PUT /users/{id}",
            "delete_user": "DELETE /users/{id}",
            "search_users": "/users/search/{query}"
        }
    }