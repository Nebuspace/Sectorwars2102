"""Dynamic Region Manager Service for Multi-Regional Platform"""

import asyncio
import logging
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from typing import Dict, List, Optional
import os
import redis.asyncio as redis
import psycopg2
import json
from datetime import datetime

from region_provisioner import RegionProvisioner
from monitoring import RegionMonitor
from config import get_settings
from models import RegionRequest, RegionStatus, ScalingRequest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/logs/region-manager.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="SectorWars Region Manager",
    description="Dynamic provisioning and management of regional game servers",
    version="1.0.0"
)

# Add CORS middleware
allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if os.environ.get("CORS_ALLOWED_ORIGINS") else ["http://localhost:3000", "http://localhost:3001"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
settings = get_settings()
provisioner = RegionProvisioner()
monitor = RegionMonitor()

# Global state
active_regions: Dict[str, RegionStatus] = {}

# Tracks consecutive low-utilization monitoring samples per region so that
# scale-down only fires after a sustained cool-down window rather than on a
# single transient dip. Keyed by region_name -> consecutive low-sample count.
_scale_down_low_samples: Dict[str, int] = {}

# Number of consecutive low-utilization samples required before scaling down.
# At the 30s monitoring cadence this is a ~3 minute sustained-low window,
# deliberately conservative so brief idle periods don't thrash container
# resources. Scale-up, by contrast, reacts on a single sample.
SCALE_DOWN_SUSTAINED_SAMPLES: int = int(
    os.environ.get("SCALE_DOWN_SUSTAINED_SAMPLES", "6")
)


@app.on_event("startup")
async def startup_event():
    """Initialize the region manager on startup"""
    logger.info("Starting Region Manager Service...")
    
    # Initialize monitoring
    await monitor.initialize()
    
    # Load existing regions from database
    try:
        existing_regions = await provisioner.get_active_regions()
        for region in existing_regions:
            active_regions[region.name] = RegionStatus(
                name=region.name,
                status="active",
                owner_id=region.owner_id,
                container_id=region.container_id if hasattr(region, 'container_id') else None,
                created_at=region.created_at,
                player_count=0,
                resource_usage={}
            )
        
        logger.info(f"Loaded {len(active_regions)} existing regions")
    except Exception as e:
        logger.error(f"Failed to load existing regions: {e}")
    
    # Start background monitoring
    asyncio.create_task(monitor_regions_loop())
    logger.info("Region Manager Service started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Region Manager Service...")
    await monitor.cleanup()


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_regions": len(active_regions),
        "service": "region-manager",
        "version": "1.0.0"
    }


@app.post("/regions/provision")
async def provision_region(
    request: RegionRequest,
    background_tasks: BackgroundTasks
) -> Dict[str, str]:
    """Provision a new regional server"""
    try:
        logger.info(f"Provisioning region: {request.name} for owner: {request.owner_id}")
        
        # Check if region already exists
        if request.name in active_regions:
            raise HTTPException(status_code=409, detail="Region already exists")
        
        # Validate region request
        if not await provisioner.validate_region_request(request):
            raise HTTPException(status_code=400, detail="Invalid region request")
        
        # Add to tracking immediately
        active_regions[request.name] = RegionStatus(
            name=request.name,
            status="provisioning",
            owner_id=request.owner_id,
            container_id=None,
            created_at=None,
            player_count=0,
            resource_usage={}
        )
        
        # Start provisioning in background
        background_tasks.add_task(provision_region_task, request)
        
        return {
            "message": "Region provisioning started",
            "region_name": request.name,
            "status": "provisioning"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start region provisioning: {e}")
        raise HTTPException(status_code=500, detail="Failed to start provisioning")


@app.delete("/regions/{region_name}")
async def terminate_region(
    region_name: str,
    background_tasks: BackgroundTasks
) -> Dict[str, str]:
    """Terminate a regional server"""
    try:
        if region_name not in active_regions:
            raise HTTPException(status_code=404, detail="Region not found")
        
        # Update status
        active_regions[region_name].status = "terminating"
        
        # Start termination in background
        background_tasks.add_task(terminate_region_task, region_name)
        
        return {
            "message": "Region termination started",
            "region_name": region_name,
            "status": "terminating"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start region termination: {e}")
        raise HTTPException(status_code=500, detail="Failed to start termination")


@app.get("/regions")
async def list_regions() -> List[RegionStatus]:
    """List all active regions"""
    return list(active_regions.values())


@app.get("/regions/{region_name}")
async def get_region_status(region_name: str) -> RegionStatus:
    """Get status of a specific region"""
    if region_name not in active_regions:
        raise HTTPException(status_code=404, detail="Region not found")
    
    return active_regions[region_name]


@app.post("/regions/{region_name}/scale")
async def scale_region(
    region_name: str,
    request: ScalingRequest,
    background_tasks: BackgroundTasks
) -> Dict[str, str]:
    """Scale regional server resources"""
    try:
        if region_name not in active_regions:
            raise HTTPException(status_code=404, detail="Region not found")
        
        # Start scaling in background
        background_tasks.add_task(scale_region_task, region_name, request)
        
        return {
            "message": "Region scaling started",
            "region_name": region_name,
            "target_resources": request.dict()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to start region scaling: {e}")
        raise HTTPException(status_code=500, detail="Failed to start scaling")


@app.get("/metrics")
async def get_metrics() -> Dict[str, any]:
    """Get platform-wide metrics"""
    try:
        total_regions = len(active_regions)
        active_count = len([r for r in active_regions.values() if r.status == "active"])
        total_players = sum(r.player_count for r in active_regions.values())
        
        # Get resource usage
        total_cpu = sum(
            r.resource_usage.get("cpu_percent", 0) 
            for r in active_regions.values() 
            if r.resource_usage
        )
        total_memory = sum(
            r.resource_usage.get("memory_mb", 0) 
            for r in active_regions.values() 
            if r.resource_usage
        )
        
        return {
            "total_regions": total_regions,
            "active_regions": active_count,
            "total_players": total_players,
            "average_cpu_usage": total_cpu / max(active_count, 1),
            "total_memory_usage_mb": total_memory,
            "regions": list(active_regions.values())
        }
    
    except Exception as e:
        logger.error(f"Failed to get metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get metrics")


# Background task functions
async def provision_region_task(request: RegionRequest):
    """Background task to provision a region"""
    try:
        logger.info(f"Starting region provisioning for: {request.name}")
        
        # Create database for region
        db_created = await provisioner.create_region_database(request.name)
        if not db_created:
            raise Exception("Failed to create region database")
        
        # Generate region configuration
        config = await provisioner.generate_region_config(request)
        
        # Start containers
        container_info = await provisioner.start_region_containers(request.name, config)
        
        # Update region status
        if request.name in active_regions:
            active_regions[request.name].status = "active"
            active_regions[request.name].container_id = container_info.get("game_server_id")
            active_regions[request.name].created_at = container_info.get("created_at")
        
        # Register with Central Nexus
        await provisioner.register_with_nexus(request.name, container_info)
        
        logger.info(f"Region {request.name} provisioned successfully")
        
    except Exception as e:
        logger.error(f"Failed to provision region {request.name}: {e}")
        if request.name in active_regions:
            active_regions[request.name].status = "failed"


async def terminate_region_task(region_name: str):
    """Background task to terminate a region"""
    try:
        logger.info(f"Starting region termination for: {region_name}")
        
        # Gracefully shutdown containers
        await provisioner.stop_region_containers(region_name)
        
        # Cleanup database
        await provisioner.cleanup_region_database(region_name)
        
        # Unregister from Central Nexus
        await provisioner.unregister_from_nexus(region_name)
        
        # Remove from active regions
        if region_name in active_regions:
            del active_regions[region_name]
        
        logger.info(f"Region {region_name} terminated successfully")
        
    except Exception as e:
        logger.error(f"Failed to terminate region {region_name}: {e}")


async def scale_region_task(region_name: str, request: ScalingRequest):
    """Background task to scale a region"""
    try:
        logger.info(f"Starting region scaling for: {region_name}")
        
        # Update container resources
        await provisioner.update_container_resources(
            region_name,
            cpu_limit=request.cpu_cores,
            memory_limit=request.memory_gb * 1024,  # Convert to MB
            disk_limit=request.disk_gb * 1024  # Convert to MB
        )
        
        logger.info(f"Region {region_name} scaled successfully")
        
    except Exception as e:
        logger.error(f"Failed to scale region {region_name}: {e}")


async def monitor_regions_loop():
    """Background loop to monitor all regions"""
    while True:
        try:
            await asyncio.sleep(30)  # Monitor every 30 seconds
            
            for region_name in list(active_regions.keys()):
                try:
                    # Get container stats
                    stats = await monitor.get_container_stats(
                        active_regions[region_name].container_id
                    )
                    
                    if stats:
                        active_regions[region_name].resource_usage = stats
                        
                        # Get player count from region API
                        player_count = await monitor.get_region_player_count(region_name)
                        active_regions[region_name].player_count = player_count
                        
                        # Check for auto-scaling triggers
                        await check_auto_scaling(region_name, stats)
                
                except Exception as e:
                    logger.warning(f"Failed to monitor region {region_name}: {e}")
        
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")


async def check_auto_scaling(region_name: str, stats: Dict[str, any]):
    """Check if a region needs auto-scaling"""
    try:
        cpu_usage = stats.get("cpu_percent", 0)
        memory_usage = stats.get("memory_percent", 0)
        
        # Auto-scale up if CPU > 80% or Memory > 85%
        if cpu_usage > 80 or memory_usage > 85:
            logger.info(f"Auto-scaling up region {region_name} - CPU: {cpu_usage}%, Memory: {memory_usage}%")
            
            current_region = active_regions[region_name]
            current_cpu = current_region.resource_usage.get("cpu_cores", 2)
            current_memory = current_region.resource_usage.get("memory_gb", 4)
            
            scale_request = ScalingRequest(
                cpu_cores=min(current_cpu * 1.5, 8),  # Max 8 cores
                memory_gb=min(current_memory * 1.3, 16),  # Max 16GB
                disk_gb=current_region.resource_usage.get("disk_gb", 20)
            )
            
            await scale_region_task(region_name, scale_request)
            # Any upward pressure resets the sustained-low counter.
            _scale_down_low_samples.pop(region_name, None)

        # Auto-scale down if CPU < 20% and Memory < 30% for a sustained period.
        # Symmetric to scale-up but gated on a cool-down window: a region must
        # stay below both thresholds for SCALE_DOWN_SUSTAINED_SAMPLES consecutive
        # monitoring samples before we reclaim resources, avoiding thrash.
        elif (cpu_usage < settings.SCALE_DOWN_CPU_THRESHOLD
              and memory_usage < settings.SCALE_DOWN_MEMORY_THRESHOLD):
            low_samples = _scale_down_low_samples.get(region_name, 0) + 1
            _scale_down_low_samples[region_name] = low_samples

            if low_samples < SCALE_DOWN_SUSTAINED_SAMPLES:
                logger.debug(
                    f"Region {region_name} under scale-down thresholds "
                    f"(CPU: {cpu_usage}%, Memory: {memory_usage}%) - "
                    f"sustained sample {low_samples}/{SCALE_DOWN_SUSTAINED_SAMPLES}"
                )
                return

            current_region = active_regions[region_name]
            current_cpu = current_region.resource_usage.get(
                "cpu_cores", settings.DEFAULT_CPU_CORES
            )
            current_memory = current_region.resource_usage.get(
                "memory_gb", settings.DEFAULT_MEMORY_GB
            )

            # Symmetric inverse of the scale-up factors, floored at the region
            # baseline minimums (CPU 1 core, memory 2 GB per ScalingRequest
            # bounds) so a quiet region is never starved below a workable size.
            target_cpu = max(round(current_cpu / 1.5, 1), 1.0)
            target_memory = max(int(current_memory / 1.3), 2)

            # Nothing to reclaim if we're already at the floor.
            if target_cpu >= current_cpu and target_memory >= current_memory:
                logger.debug(
                    f"Region {region_name} already at minimum allocation - "
                    f"skipping scale-down"
                )
                _scale_down_low_samples.pop(region_name, None)
                return

            logger.info(
                f"Auto-scaling down region {region_name} after "
                f"{low_samples} sustained low samples - "
                f"CPU: {cpu_usage}%, Memory: {memory_usage}% "
                f"({current_cpu}->{target_cpu} cores, "
                f"{current_memory}->{target_memory} GB)"
            )

            scale_request = ScalingRequest(
                cpu_cores=target_cpu,
                memory_gb=target_memory,
                disk_gb=current_region.resource_usage.get(
                    "disk_gb", settings.DEFAULT_DISK_GB
                )
            )

            await scale_region_task(region_name, scale_request)
            # Reset after acting so the next scale-down also requires a fresh
            # sustained window.
            _scale_down_low_samples.pop(region_name, None)

        else:
            # Utilization is in the comfortable mid-band; clear any partial
            # sustained-low streak.
            _scale_down_low_samples.pop(region_name, None)

    except Exception as e:
        logger.error(f"Error in auto-scaling check for {region_name}: {e}")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8081,
        log_level="info",
        reload=False
    )