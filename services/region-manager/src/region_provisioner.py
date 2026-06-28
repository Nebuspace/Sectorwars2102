"""Region Provisioner - Handles dynamic creation and management of regional containers"""

import asyncio
import docker
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import asyncpg
import redis.asyncio as redis
from jinja2 import Environment, FileSystemLoader
import yaml
import os

from models import RegionRequest, RegionConfig
from config import get_settings

logger = logging.getLogger(__name__)


class RegionProvisioner:
    """Handles provisioning and management of regional containers"""
    
    def __init__(self):
        self.settings = get_settings()
        self.docker_client = docker.from_env()
        self.template_env = Environment(loader=FileSystemLoader('/app/templates'))
        self._redis_pool = None
        self._db_pool = None
    
    async def initialize(self):
        """Initialize connections"""
        # Redis connection
        self._redis_pool = redis.ConnectionPool.from_url(
            self.settings.REDIS_URL,
            decode_responses=True
        )
        
        # Database connection pool
        self._db_pool = await asyncpg.create_pool(
            self.settings.DATABASE_URL,
            min_size=5,
            max_size=10
        )
    
    async def get_active_regions(self) -> List[Any]:
        """Get list of active regions from database"""
        if not self._db_pool:
            await self.initialize()
        
        async with self._db_pool.acquire() as conn:
            regions = await conn.fetch("""
                SELECT id, name, owner_id, status, created_at, paypal_subscription_id
                FROM regions 
                WHERE status = 'active'
            """)
            return regions
    
    async def validate_region_request(self, request: RegionRequest) -> bool:
        """Validate region provisioning request"""
        try:
            # Check if region name is valid
            if not request.name.replace("-", "").replace("_", "").isalnum():
                return False
            
            # Check if name is not reserved
            reserved_names = [
                "central-nexus", "admin", "api", "system", "default",
                "test", "staging", "production"
            ]
            if request.name.lower() in reserved_names:
                return False
            
            # Check if owner has valid subscription
            if not self._db_pool:
                await self.initialize()
            
            async with self._db_pool.acquire() as conn:
                subscription = await conn.fetchrow("""
                    SELECT paypal_subscription_id, subscription_status
                    FROM users 
                    WHERE id = $1 AND subscription_tier = 'regional_owner'
                """, request.owner_id)
                
                if not subscription or subscription['subscription_status'] != 'ACTIVE':
                    return False
            
            return True
        
        except Exception as e:
            logger.error(f"Failed to validate region request: {e}")
            return False
    
    async def create_region_database(self, region_name: str) -> bool:
        """Create isolated database for the region"""
        try:
            # Connect to PostgreSQL as superuser
            conn = await asyncpg.connect(
                host=self.settings.DB_HOST,
                port=self.settings.DB_PORT,
                user=self.settings.DB_SUPERUSER,
                password=self.settings.DB_SUPERUSER_PASSWORD,
                database="postgres"
            )
            
            db_name = f"region_{region_name.replace('-', '_')}"
            username = f"region_{region_name.replace('-', '_')}_user"
            password = f"region_password_{region_name}_{datetime.now().strftime('%Y%m%d')}"
            
            try:
                # Create database
                await conn.execute(f'CREATE DATABASE "{db_name}"')
                
                # Create user
                await conn.execute(f"""
                    CREATE USER "{username}" WITH PASSWORD '{password}'
                """)
                
                # Grant permissions
                await conn.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{db_name}" TO "{username}"')
                
                logger.info(f"Created database {db_name} for region {region_name}")
                
                # Store credentials in Redis for later use
                redis_client = redis.Redis(connection_pool=self._redis_pool)
                await redis_client.hset(
                    f"region:{region_name}:db",
                    mapping={
                        "database": db_name,
                        "username": username,
                        "password": password,
                        "host": self.settings.DB_HOST,
                        "port": self.settings.DB_PORT
                    }
                )
                
                return True
            
            finally:
                await conn.close()
        
        except Exception as e:
            logger.error(f"Failed to create database for region {region_name}: {e}")
            return False
    
    async def generate_region_config(self, request: RegionRequest) -> RegionConfig:
        """Generate configuration for regional containers"""
        try:
            # Get database credentials
            redis_client = redis.Redis(connection_pool=self._redis_pool)
            db_config = await redis_client.hgetall(f"region:{request.name}:db")
            
            # Generate container configuration
            config = RegionConfig(
                region_name=request.name,
                owner_id=request.owner_id,
                database_url=f"postgresql://{db_config['username']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['database']}",
                redis_url=f"{self.settings.REDIS_URL}/{request.redis_db_index or 3}",
                cpu_cores=request.cpu_cores,
                memory_gb=request.memory_gb,
                disk_gb=request.disk_gb,
                max_players=request.max_players,
                governance_type=request.governance_type,
                economic_specialization=request.economic_specialization,
                language_pack=request.language_pack or {},
                aesthetic_theme=request.aesthetic_theme or {},
                starting_credits=request.starting_credits,
                starting_ship=request.starting_ship,
                custom_rules=request.custom_rules or {}
            )
            
            return config
        
        except Exception as e:
            logger.error(f"Failed to generate config for region {request.name}: {e}")
            raise
    
    async def start_region_containers(self, region_name: str, config: RegionConfig) -> Dict[str, str]:
        """Start containers for the region"""
        try:
            # Generate Docker Compose configuration from template
            compose_template = self.template_env.get_template('docker-compose.region.yml.j2')
            compose_content = compose_template.render(config=config)
            
            # Write compose file to temporary location
            compose_file_path = f"/tmp/docker-compose.{region_name}.yml"
            with open(compose_file_path, 'w') as f:
                f.write(compose_content)
            
            # Start containers using Docker Compose
            result = await asyncio.create_subprocess_exec(
                'docker-compose',
                '-f', compose_file_path,
                '-p', f'sectorwars-region-{region_name}',
                'up', '-d',
                capture_output=True,
                text=True
            )
            
            stdout, stderr = await result.communicate()
            
            if result.returncode != 0:
                raise Exception(f"Docker Compose failed: {stderr}")
            
            # Get container information
            containers = self.docker_client.containers.list(
                filters={"label": f"region={region_name}"}
            )
            
            container_info = {
                "region_name": region_name,
                "created_at": datetime.utcnow(),
                "containers": []
            }
            
            for container in containers:
                container_info["containers"].append({
                    "id": container.id,
                    "name": container.name,
                    "status": container.status,
                    "ports": container.ports
                })
                
                # Store game server container ID
                if "gameserver" in container.name:
                    container_info["game_server_id"] = container.id
            
            logger.info(f"Started containers for region {region_name}")
            return container_info
        
        except Exception as e:
            logger.error(f"Failed to start containers for region {region_name}: {e}")
            raise
    
    async def stop_region_containers(self, region_name: str):
        """Stop and remove containers for the region"""
        try:
            # Get containers for the region
            containers = self.docker_client.containers.list(
                all=True,
                filters={"label": f"region={region_name}"}
            )
            
            for container in containers:
                try:
                    logger.info(f"Stopping container {container.name}")
                    container.stop(timeout=30)
                    container.remove(v=True)  # Remove volumes
                except Exception as e:
                    logger.warning(f"Failed to stop container {container.name}: {e}")
            
            # Remove networks
            try:
                network = self.docker_client.networks.get(f"sectorwars-region-{region_name}_default")
                network.remove()
            except Exception as e:
                logger.warning(f"Failed to remove network for region {region_name}: {e}")
            
            logger.info(f"Stopped containers for region {region_name}")
        
        except Exception as e:
            logger.error(f"Failed to stop containers for region {region_name}: {e}")
            raise
    
    async def cleanup_region_database(self, region_name: str):
        """Cleanup region database and user"""
        try:
            # Get database credentials
            redis_client = redis.Redis(connection_pool=self._redis_pool)
            db_config = await redis_client.hgetall(f"region:{region_name}:db")
            
            if not db_config:
                logger.warning(f"No database config found for region {region_name}")
                return
            
            # Connect as superuser
            conn = await asyncpg.connect(
                host=self.settings.DB_HOST,
                port=self.settings.DB_PORT,
                user=self.settings.DB_SUPERUSER,
                password=self.settings.DB_SUPERUSER_PASSWORD,
                database="postgres"
            )
            
            try:
                # Terminate connections to the database
                await conn.execute(f"""
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = '{db_config['database']}'
                    AND pid <> pg_backend_pid()
                """)
                
                # Drop database
                await conn.execute(f'DROP DATABASE IF EXISTS "{db_config["database"]}"')
                
                # Drop user
                await conn.execute(f'DROP USER IF EXISTS "{db_config["username"]}"')
                
                logger.info(f"Cleaned up database for region {region_name}")
            
            finally:
                await conn.close()
            
            # Remove credentials from Redis
            await redis_client.delete(f"region:{region_name}:db")
        
        except Exception as e:
            logger.error(f"Failed to cleanup database for region {region_name}: {e}")
    
    async def register_with_nexus(self, region_name: str, container_info: Dict[str, str]):
        """Register the new region with Central Nexus"""
        try:
            # Update region status in database
            if not self._db_pool:
                await self.initialize()
            
            async with self._db_pool.acquire() as conn:
                await conn.execute("""
                    UPDATE regions 
                    SET status = 'active',
                        updated_at = CURRENT_TIMESTAMP,
                        nexus_warp_sector = $2
                    WHERE name = $1
                """, region_name, 250)  # Central sector for warp gate
            
            # Register with service discovery
            redis_client = redis.Redis(connection_pool=self._redis_pool)
            await redis_client.hset(
                f"regions:active:{region_name}",
                mapping={
                    "container_id": container_info.get("game_server_id"),
                    "status": "active",
                    "registered_at": datetime.utcnow().isoformat(),
                    "endpoint": f"http://region-{region_name}-server:8080"
                }
            )
            
            logger.info(f"Registered region {region_name} with Central Nexus")
        
        except Exception as e:
            logger.error(f"Failed to register region {region_name} with Nexus: {e}")
            raise
    
    async def unregister_from_nexus(self, region_name: str):
        """Unregister region from Central Nexus"""
        try:
            # Update region status in database
            if not self._db_pool:
                await self.initialize()
            
            async with self._db_pool.acquire() as conn:
                await conn.execute("""
                    UPDATE regions 
                    SET status = 'terminated',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE name = $1
                """, region_name)
            
            # Remove from service discovery
            redis_client = redis.Redis(connection_pool=self._redis_pool)
            await redis_client.delete(f"regions:active:{region_name}")
            
            logger.info(f"Unregistered region {region_name} from Central Nexus")
        
        except Exception as e:
            logger.error(f"Failed to unregister region {region_name} from Nexus: {e}")
    
    async def update_container_resources(
        self,
        region_name: str,
        cpu_limit: float,
        memory_limit: int,
        disk_limit: int
    ):
        """Update resource limits for region containers"""
        try:
            containers = self.docker_client.containers.list(
                filters={"label": f"region={region_name}"}
            )
            
            for container in containers:
                # Update container resources using Docker API
                container.update(
                    cpu_period=100000,
                    cpu_quota=int(cpu_limit * 100000),
                    mem_limit=f"{memory_limit}m"
                )
            
            logger.info(f"Updated resources for region {region_name}")
        
        except Exception as e:
            logger.error(f"Failed to update resources for region {region_name}: {e}")
            raise