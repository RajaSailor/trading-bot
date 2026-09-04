"""
BACKUP & DATA MANAGEMENT SYSTEM
Automated backups, data export, and disaster recovery
File: backup_manager.py
"""

import os
import logging
import json
import shutil
import gzip
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from screener_background import screener_state, get_ist_time
import tarfile

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path="./.env", override=True)

# ============================================================================
# BACKUP CONFIGURATION
# ============================================================================

BACKUP_DIR = Path("./backups")
DATA_DIR = Path("./data")
LOGS_DIR = Path("./logs")
PRACTICE_TRADES_DIR = Path("./practice_trades")
AUTO_TRADES_DIR = Path("./auto_trades")

# Create directories
BACKUP_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

backup_config = {
    "backup_interval_hours": 6,
    "max_backups_retain": 10,
    "backup_enabled": True,
    "compression": True,
    "last_backup_time": None,
    "backup_history": [],
}

# ============================================================================
# BACKUP FUNCTIONS
# ============================================================================

def create_screener_backup():
    """Create backup of screener state and configuration"""
    ist_time = get_ist_time()
    backup_name = f"screener_{ist_time.strftime('%Y%m%d_%H%M%S')}"
    backup_path = BACKUP_DIR / backup_name
    backup_path.mkdir(exist_ok=True)
    
    try:
        # Backup screener state
        state_file = backup_path / "screener_state.json"
        with open(state_file, 'w') as f:
            json.dump(screener_state, f, indent=2, default=str)
        
        logger.info(f"✅ Screener state backed up: {state_file}")
        
        # Backup environment variables
        env_file = Path("./.env")
        if env_file.exists():
            shutil.copy(env_file, backup_path / ".env.backup")
            logger.info(f"✅ Environment config backed up")
        
        # Backup strategy configuration
        strategy_file = Path("./strategy.py")
        if strategy_file.exists():
            shutil.copy(strategy_file, backup_path / "strategy.py")
            logger.info(f"✅ Strategy file backed up")
        
        return str(backup_path)
    
    except Exception as e:
        logger.error(f"[ERROR] Screener backup failed: {e}")
        return None

def backup_trade_data():
    """Backup all trade data"""
    ist_time = get_ist_time()
    backup_name = f"trades_{ist_time.strftime('%Y%m%d_%H%M%S')}"
    backup_path = BACKUP_DIR / backup_name
    backup_path.mkdir(exist_ok=True)
    
    try:
        # Backup practice trades
        if PRACTICE_TRADES_DIR.exists():
            practice_backup = backup_path / "practice_trades"
            shutil.copytree(PRACTICE_TRADES_DIR, practice_backup)
            logger.info(f"✅ Practice trades backed up")
        
        # Backup auto trades
        if AUTO_TRADES_DIR.exists():
            auto_backup = backup_path / "auto_trades"
            shutil.copytree(AUTO_TRADES_DIR, auto_backup)
            logger.info(f"✅ Auto trades backed up")
        
        return str(backup_path)
    
    except Exception as e:
        logger.error(f"[ERROR] Trade data backup failed: {e}")
        return None

def backup_logs():
    """Backup application logs"""
    ist_time = get_ist_time()
    backup_name = f"logs_{ist_time.strftime('%Y%m%d_%H%M%S')}"
    backup_path = BACKUP_DIR / backup_name
    backup_path.mkdir(exist_ok=True)
    
    try:
        if LOGS_DIR.exists():
            for log_file in LOGS_DIR.glob("*.log"):
                shutil.copy(log_file, backup_path / log_file.name)
            logger.info(f"✅ Logs backed up")
        
        return str(backup_path)
    
    except Exception as e:
        logger.error(f"[ERROR] Logs backup failed: {e}")
        return None

def create_full_backup():
    """Create complete backup of everything"""
    ist_time = get_ist_time()
    backup_timestamp = ist_time.strftime('%Y%m%d_%H%M%S')
    backup_name = f"full_backup_{backup_timestamp}"
    backup_path = BACKUP_DIR / backup_name
    backup_path.mkdir(exist_ok=True)
    
    logger.info(f"\n{'='*80}")
    logger.info(f"💾 CREATING FULL BACKUP")
    logger.info(f"{'='*80}")
    logger.info(f"Backup Name: {backup_name}")
    logger.info(f"Time: {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info(f"Path: {backup_path}")
    
    try:
        # Backup screener
        screener_backup = create_screener_backup()
        
        # Backup trades
        trades_backup = backup_trade_data()
        
        # Backup logs
        logs_backup = backup_logs()
        
        # Backup application files
        app_files = backup_path / "app_files"
        app_files.mkdir(exist_ok=True)
        
        app_configs = [
            "screener_app.py",
            "screener_background.py",
            "strategy.py",
            "mobile_control.py",
            "practice_trading.py",
            "automated_trading.py",
            "requirements.txt",
            ".env",
        ]
        
        for config_file in app_configs:
            file_path = Path(f"./{config_file}")
            if file_path.exists():
                shutil.copy(file_path, app_files / config_file)
        
        logger.info(f"✅ Application files backed up")
        
        # Create compressed archive
        if backup_config["compression"]:
            archive_path = f"{backup_path}.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(backup_path, arcname=backup_name)
            logger.info(f"✅ Backup compressed: {archive_path}")
            
            # Remove uncompressed backup to save space
            shutil.rmtree(backup_path)
            logger.info(f"✅ Uncompressed backup removed")
            
            backup_config["last_backup_time"] = ist_time.isoformat()
            backup_config["backup_history"].append({
                "timestamp": ist_time.isoformat(),
                "backup_name": backup_name,
                "size": os.path.getsize(archive_path),
                "compressed": True,
            })
        
        else:
            backup_config["last_backup_time"] = ist_time.isoformat()
            backup_config["backup_history"].append({
                "timestamp": ist_time.isoformat(),
                "backup_name": backup_name,
                "size": sum(f.stat().st_size for f in backup_path.rglob('*') if f.is_file()),
                "compressed": False,
            })
        
        # Cleanup old backups
        cleanup_old_backups()
        
        # Save backup config
        save_backup_config()
        
        logger.info(f"{'='*80}")
        logger.info(f"✅ FULL BACKUP COMPLETED SUCCESSFULLY!")
        logger.info(f"{'='*80}\n")
        
        return str(backup_path)
    
    except Exception as e:
        logger.error(f"[ERROR] Full backup failed: {e}")
        return None

def cleanup_old_backups():
    """Remove old backups to save space"""
    try:
        backups = sorted(BACKUP_DIR.glob("*.tar.gz"), key=os.path.getctime, reverse=True)
        
        if len(backups) > backup_config["max_backups_retain"]:
            to_delete = backups[backup_config["max_backups_retain"]:]
            for backup in to_delete:
                os.remove(backup)
                logger.info(f"🗑️ Deleted old backup: {backup.name}")
    
    except Exception as e:
        logger.error(f"[ERROR] Backup cleanup failed: {e}")

# ============================================================================
# RESTORE FUNCTIONS
# ============================================================================

def restore_from_backup(backup_name):
    """Restore data from backup"""
    ist_time = get_ist_time()
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🔄 RESTORING FROM BACKUP")
    logger.info(f"{'='*80}")
    logger.info(f"Backup: {backup_name}")
    logger.info(f"Time: {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    try:
        backup_path = BACKUP_DIR / f"{backup_name}.tar.gz"
        
        if not backup_path.exists():
            logger.error(f"[ERROR] Backup not found: {backup_path}")
            return False
        
        # Extract backup
        restore_path = BACKUP_DIR / f"restore_{ist_time.strftime('%Y%m%d_%H%M%S')}"
        
        with tarfile.open(backup_path, "r:gz") as tar:
            tar.extractall(restore_path)
        
        logger.info(f"✅ Backup extracted to {restore_path}")
        logger.info(f"{'='*80}")
        logger.info(f"✅ RESTORE COMPLETED!")
        logger.info(f"{'='*80}\n")
        
        return True
    
    except Exception as e:
        logger.error(f"[ERROR] Restore failed: {e}")
        return False

def list_backups():
    """List all available backups"""
    backups = sorted(BACKUP_DIR.glob("*.tar.gz"), key=os.path.getctime, reverse=True)
    
    logger.info(f"\n{'='*80}")
    logger.info(f"📋 AVAILABLE BACKUPS")
    logger.info(f"{'='*80}")
    
    if not backups:
        logger.info("No backups found")
        return []
    
    backup_list = []
    for i, backup in enumerate(backups, 1):
        size_mb = os.path.getsize(backup) / (1024 * 1024)
        create_time = datetime.fromtimestamp(os.path.getctime(backup))
        
        logger.info(f"{i}. {backup.name}")
        logger.info(f"   Size: {size_mb:.2f} MB")
        logger.info(f"   Created: {create_time}")
        logger.info(f"")
        
        backup_list.append({
            "name": backup.stem,
            "size": size_mb,
            "created": create_time.isoformat(),
        })
    
    logger.info(f"{'='*80}\n")
    return backup_list

# ============================================================================
# DATA EXPORT FUNCTIONS
# ============================================================================

def export_to_csv():
    """Export trade data to CSV"""
    ist_time = get_ist_time()
    export_path = DATA_DIR / f"export_{ist_time.strftime('%Y%m%d_%H%M%S')}.csv"
    
    try:
        # This would require pandas - implementing basic CSV export
        import csv
        
        with open(export_path, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Timestamp', 'Type', 'Data'])
            writer.writerow([ist_time.isoformat(), 'Screener State', screener_state])
        
        logger.info(f"✅ Data exported to CSV: {export_path}")
        return str(export_path)
    
    except Exception as e:
        logger.error(f"[ERROR] CSV export failed: {e}")
        return None

def export_to_json():
    """Export all data to JSON"""
    ist_time = get_ist_time()
    export_path = DATA_DIR / f"export_{ist_time.strftime('%Y%m%d_%H%M%S')}.json"
    
    try:
        export_data = {
            "export_time": ist_time.isoformat(),
            "screener_state": screener_state,
            "backup_config": backup_config,
        }
        
        with open(export_path, 'w') as f:
            json.dump(export_data, f, indent=2, default=str)
        
        logger.info(f"✅ Data exported to JSON: {export_path}")
        return str(export_path)
    
    except Exception as e:
        logger.error(f"[ERROR] JSON export failed: {e}")
        return None

# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

def save_backup_config():
    """Save backup configuration"""
    try:
        config_file = BACKUP_DIR / "backup_config.json"
        with open(config_file, 'w') as f:
            json.dump(backup_config, f, indent=2, default=str)
        logger.info(f"✅ Backup config saved")
    except Exception as e:
        logger.error(f"[ERROR] Failed to save backup config: {e}")

def load_backup_config():
    """Load backup configuration"""
    global backup_config
    try:
        config_file = BACKUP_DIR / "backup_config.json"
        if config_file.exists():
            with open(config_file, 'r') as f:
                backup_config = json.load(f)
            logger.info(f"✅ Backup config loaded")
    except Exception as e:
        logger.error(f"[ERROR] Failed to load backup config: {e}")

def generate_backup_report():
    """Generate backup status report"""
    ist_time = get_ist_time()
    
    backups = list_backups()
    total_size = sum([b["size"] for b in backups])
    
    report = f"""
╔════════════════════════════════════════════════════════════════════════════╗
║                      💾 BACKUP & DATA MANAGEMENT REPORT                    ║
╚════════════════════════════════════════════════════════════════════════════╝

📅 Report Generated: {ist_time.strftime('%Y-%m-%d %H:%M:%S %Z')}

┌─ 📊 BACKUP STATISTICS ───────────────────────────────────────────────────┐
│  Total Backups: {len(backups)}
│  Total Size: {total_size:.2f} MB
│  Max Backups Retained: {backup_config['max_backups_retain']}
│  Backup Interval: {backup_config['backup_interval_hours']} hours
│  Compression: {'✅ Enabled' if backup_config['compression'] else '❌ Disabled'}
│  Last Backup: {backup_config['last_backup_time']}
└──────────────────────────────────────────────────────────────────────────┘

┌─ 🗂️ BACKUP HISTORY ──────────────────────────────────────────────────────┐
"""
    
    for backup in backups[:5]:  # Show last 5 backups
        report += f"\n│  📦 {backup['name']}\n"
        report += f"│     Size: {backup['size']:.2f} MB\n"
        report += f"│     Created: {backup['created']}\n"
    
    report += "\n└──────────────────────────────────────────────────────────────────────────┘\n"
    
    report += f"""
┌─ 🔧 BACKUP SETTINGS ─────────────────────────────────────────────────────┐
│  Status: {'✅ ENABLED' if backup_config['backup_enabled'] else '❌ DISABLED'}
│  Auto Backup: Every {backup_config['backup_interval_hours']} hours
│  Retention: Last {backup_config['max_backups_retain']} backups
│  Compression: {'✅ Enabled' if backup_config['compression'] else '❌ Disabled'}
└──────────────────────────────────────────────────────────────────────────┘

📝 INCLUDED IN BACKUPS:
  ✅ Screener state & configuration
  ✅ All trade data (practice & automated)
  ✅ Application logs
  ✅ Strategy configuration
  ✅ Environment variables (.env)
  ✅ Application source code
"""
    
    return report

# ============================================================================
# INITIALIZATION
# ============================================================================

def initialize_backup_manager():
    """Initialize backup system"""
    logger.info("=" * 80)
    logger.info("💾 INITIALIZING BACKUP & DATA MANAGEMENT")
    logger.info("=" * 80)
    logger.info(f"Backup Directory: {BACKUP_DIR}")
    logger.info(f"Data Directory: {DATA_DIR}")
    logger.info(f"Max Backups: {backup_config['max_backups_retain']}")
    logger.info(f"Auto Backup Interval: {backup_config['backup_interval_hours']} hours")
    logger.info("=" * 80)
    
    # Load existing configuration
    load_backup_config()
    
    logger.info("✅ Backup manager initialized successfully!")

if __name__ == "__main__":
    initialize_backup_manager()
    print(generate_backup_report())

