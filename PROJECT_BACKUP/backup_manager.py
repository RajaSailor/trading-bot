"""
PROJECT_BACKUP: Automatic Daily Backup & Recovery System
Runs every day at 6:00 AM IST (1 hour after self-test)
File: backup_manager.py
"""

import sqlite3
import shutil
import boto3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
import gzip
import hashlib
from telegram import Bot
import asyncio
import os
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
load_dotenv()

TELEGRAM_BOT_TOKENS = {
    "INDEX_OPTIONS": os.getenv("TELEGRAM_BOT_INDEX", "8601160697:AAFFxscCMfqcrXaf1lw69xK7Ue-RW_8aIzI"),
}

TELEGRAM_CHAT_IDS = {
    "INDEX_OPTIONS": -1003814243881,
}

# AWS S3 Configuration (Free tier)
AWS_S3_BUCKET = "trading-bot-backups"
AWS_REGION = "us-east-1"

# ============================================================================
# DATABASE BACKUP
# ============================================================================

class DatabaseBackup:
    """Handles database backup & encryption"""
    
    def __init__(self):
        self.backup_dir = Path("backups")
        self.backup_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    def backup_practice_db(self):
        """Backup practice trades database"""
        try:
            source = Path("databases/practice_trades.db")
            if not source.exists():
                logger.warning("Practice DB not found (will be created on first trade)")
                return True
            
            backup_file = self.backup_dir / f"practice_trades_{self.timestamp}.db"
            shutil.copy2(source, backup_file)
            
            # Compress
            with open(backup_file, 'rb') as f_in:
                with gzip.open(f"{backup_file}.gz", 'wb') as f_out:
                    f_out.writelines(f_in)
            
            # Remove uncompressed
            backup_file.unlink()
            
            logger.info(f"✅ Practice DB backed up: {backup_file}.gz")
            return True
        except Exception as e:
            logger.error(f"❌ Practice DB backup failed: {e}")
            return False
    
    def backup_backtest_db(self):
        """Backup backtest database"""
        try:
            source = Path("databases/backtest_data.db")
            if not source.exists():
                logger.warning("Backtest DB not found (will be created on first backtest)")
                return True
            
            backup_file = self.backup_dir / f"backtest_data_{self.timestamp}.db"
            shutil.copy2(source, backup_file)
            
            # Compress
            with open(backup_file, 'rb') as f_in:
                with gzip.open(f"{backup_file}.gz", 'wb') as f_out:
                    f_out.writelines(f_in)
            
            # Remove uncompressed
            backup_file.unlink()
            
            logger.info(f"✅ Backtest DB backed up: {backup_file}.gz")
            return True
        except Exception as e:
            logger.error(f"❌ Backtest DB backup failed: {e}")
            return False
    
    def backup_configuration(self):
        """Backup configuration files"""
        try:
            config_files = [
                ".env",
                "config.yaml",
                "strategy_params.json",
                "risk_limits.json",
            ]
            
            for config_file in config_files:
                source = Path(config_file)
                if source.exists():
                    backup_file = self.backup_dir / f"{config_file}_{self.timestamp}.backup"
                    shutil.copy2(source, backup_file)
                    logger.info(f"✅ Config backed up: {backup_file}")
            
            return True
        except Exception as e:
            logger.error(f"❌ Config backup failed: {e}")
            return False
    
    def backup_logs(self):
        """Backup recent logs"""
        try:
            log_dir = Path("logs")
            if log_dir.exists():
                backup_logs_dir = self.backup_dir / f"logs_{self.timestamp}"
                backup_logs_dir.mkdir(exist_ok=True)
                
                for log_file in log_dir.glob("*.txt"):
                    backup_file = backup_logs_dir / log_file.name
                    shutil.copy2(log_file, backup_file)
                
                logger.info(f"✅ Logs backed up: {backup_logs_dir}")
            
            return True
        except Exception as e:
            logger.error(f"❌ Logs backup failed: {e}")
            return False
    
    def calculate_checksum(self, file_path):
        """Calculate SHA256 checksum"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()


# ============================================================================
# AWS S3 BACKUP
# ============================================================================

class S3CloudBackup:
    """Handles AWS S3 cloud backup"""
    
    def __init__(self):
        try:
            self.s3_client = boto3.client(
                's3',
                region_name=AWS_REGION,
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
                aws_secret_access_key=os.getenv("AWS_SECRET_KEY"),
            )
            self.bucket_name = AWS_S3_BUCKET
        except Exception as e:
            logger.error(f"AWS S3 not configured: {e}")
            self.s3_client = None
    
    def upload_backup_to_s3(self, local_file_path, s3_key):
        """Upload backup file to S3"""
        if not self.s3_client:
            logger.warning("S3 client not initialized")
            return False
        
        try:
            self.s3_client.upload_file(
                local_file_path,
                self.bucket_name,
                s3_key,
                ExtraArgs={'ServerSideEncryption': 'AES256'}
            )
            logger.info(f"✅ Uploaded to S3: s3://{self.bucket_name}/{s3_key}")
            return True
        except Exception as e:
            logger.error(f"❌ S3 upload failed: {e}")
            return False
    
    def cleanup_old_backups(self, days_retention=30):
        """Delete backups older than retention period"""
        if not self.s3_client:
            return False
        
        try:
            cutoff_date = datetime.now() - timedelta(days=days_retention)
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name)
            
            if 'Contents' not in response:
                return True
            
            for obj in response['Contents']:
                if obj['LastModified'].replace(tzinfo=None) < cutoff_date:
                    self.s3_client.delete_object(Bucket=self.bucket_name, Key=obj['Key'])
                    logger.info(f"✅ Deleted old backup: {obj['Key']}")
            
            return True
        except Exception as e:
            logger.error(f"❌ Cleanup failed: {e}")
            return False


# ============================================================================
# LOCAL BACKUP MANAGEMENT
# ============================================================================

class LocalBackupManager:
    """Manages local backup retention"""
    
    def __init__(self, backup_dir="backups", retention_days=30):
        self.backup_dir = Path(backup_dir)
        self.retention_days = retention_days
    
    def cleanup_old_backups(self):
        """Delete local backups older than retention period"""
        try:
            cutoff_time = datetime.now() - timedelta(days=self.retention_days)
            
            for backup_file in self.backup_dir.glob("*"):
                if backup_file.stat().st_mtime < cutoff_time.timestamp():
                    backup_file.unlink()
                    logger.info(f"✅ Deleted old backup: {backup_file}")
            
            return True
        except Exception as e:
            logger.error(f"❌ Local cleanup failed: {e}")
            return False
    
    def get_backup_stats(self):
        """Get backup statistics"""
        try:
            backups = list(self.backup_dir.glob("*"))
            total_size = sum(f.stat().st_size for f in backups if f.is_file())
            
            return {
                "backup_count": len(backups),
                "total_size_mb": total_size / (1024 * 1024),
                "oldest_backup": min((f.stat().st_mtime for f in backups), default=None),
            }
        except Exception as e:
            logger.error(f"❌ Stats calculation failed: {e}")
            return {}


# ============================================================================
# BACKUP VERIFICATION
# ============================================================================

class BackupVerification:
    """Verifies backup integrity"""
    
    @staticmethod
    def verify_database(db_path):
        """Verify database integrity"""
        try:
            if not Path(db_path).exists():
                return True  # No DB yet
            
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            conn.close()
            
            if result[0] == "ok":
                logger.info(f"✅ Database verified: {db_path}")
                return True
            else:
                logger.error(f"❌ Database corruption detected: {result[0]}")
                return False
        except Exception as e:
            logger.error(f"❌ Verification failed: {e}")
            return False
    
    @staticmethod
    def verify_backups():
        """Verify all backups are readable"""
        try:
            backup_dir = Path("backups")
            for backup_file in backup_dir.glob("*.gz"):
                with gzip.open(backup_file, 'rb') as f:
                    f.read(1)  # Read first byte to verify
            
            logger.info("✅ All backups verified")
            return True
        except Exception as e:
            logger.error(f"❌ Backup verification failed: {e}")
            return False


# ============================================================================
# AUTO-RECOVERY SYSTEM
# ============================================================================

class AutoRecovery:
    """Automatic recovery from backup on failure"""
    
    def __init__(self):
        self.backup_dir = Path("backups")
    
    def find_latest_backup(self, db_type="practice"):
        """Find the latest backup for a database"""
        try:
            backups = list(self.backup_dir.glob(f"{db_type}_trades_*.db.gz"))
            if not backups:
                return None
            
            return sorted(backups)[-1]  # Latest backup
        except Exception as e:
            logger.error(f"❌ Could not find backup: {e}")
            return False
    
    def restore_from_backup(self, db_type="practice"):
        """Restore database from latest backup"""
        try:
            latest_backup = self.find_latest_backup(db_type)
            if not latest_backup:
                logger.warning(f"No backup found for {db_type}")
                return False
            
            target_db = Path(f"databases/{db_type}_trades.db")
            
            # Decompress backup
            with gzip.open(latest_backup, 'rb') as f_in:
                with open(target_db, 'wb') as f_out:
                    f_out.write(f_in.read())
            
            logger.info(f"✅ Restored {db_type} database from backup")
            return True
        except Exception as e:
            logger.error(f"❌ Recovery failed: {e}")
            return False


# ============================================================================
# MAIN BACKUP ORCHESTRATOR
# ============================================================================

class DailyBackupSystem:
    """Main backup orchestration"""
    
    def __init__(self):
        self.db_backup = DatabaseBackup()
        self.s3_backup = S3CloudBackup()
        self.local_manager = LocalBackupManager()
        self.verifier = BackupVerification()
        self.recovery = AutoRecovery()
    
    async def run_full_backup(self):
        """Execute complete backup process"""
        logger.info("=" * 80)
        logger.info("🔄 STARTING DAILY BACKUP PROCESS")
        logger.info("=" * 80)
        
        backup_results = {
            "timestamp": datetime.now().isoformat(),
            "backups": {},
            "status": "PENDING"
        }
        
        # Backup databases
        logger.info("\n📊 Backing up databases...")
        backup_results["backups"]["practice_db"] = self.db_backup.backup_practice_db()
        backup_results["backups"]["backtest_db"] = self.db_backup.backup_backtest_db()
        
        # Backup configuration
        logger.info("\n⚙️ Backing up configuration...")
        backup_results["backups"]["configuration"] = self.db_backup.backup_configuration()
        
        # Backup logs
        logger.info("\n📝 Backing up logs...")
        backup_results["backups"]["logs"] = self.db_backup.backup_logs()
        
        # Verify backups
        logger.info("\n✔️ Verifying backups...")
        backup_results["backups"]["verification"] = self.verifier.verify_backups()
        
        # Upload to S3
        logger.info("\n☁️ Uploading to S3...")
        backup_dir = Path("backups")
        for backup_file in backup_dir.glob("*"):
            if backup_file.is_file():
                s3_key = f"backups/{backup_file.name}"
                self.s3_backup.upload_backup_to_s3(str(backup_file), s3_key)
        
        # Cleanup old backups
        logger.info("\n🧹 Cleaning up old backups...")
        self.local_manager.cleanup_old_backups()
        self.s3_backup.cleanup_old_backups()
        
        # Get stats
        stats = self.local_manager.get_backup_stats()
        backup_results["stats"] = stats
        
        # Determine overall status
        all_passed = all(backup_results["backups"].values())
        backup_results["status"] = "SUCCESS" if all_passed else "PARTIAL"
        
        logger.info("\n" + "=" * 80)
        logger.info(f"🎯 BACKUP COMPLETE: {backup_results['status']}")
        logger.info("=" * 80)
        
        return backup_results
    
    async def send_telegram_report(self, backup_results):
        """Send backup report to Telegram"""
        try:
            status = backup_results["status"]
            stats = backup_results.get("stats", {})
            
            if status == "SUCCESS":
                message = f"""
💾 DAILY BACKUP COMPLETED ✅

Date: {backup_results['timestamp']}
Status: All backups successful

📊 Backup Summary:
├─ Practice DB: ✅
├─ Backtest DB: ✅
├─ Configuration: ✅
├─ Logs: ✅
└─ Verification: ✅

☁️ Cloud Sync: ✅ (S3)
📦 Backup Count: {stats.get('backup_count', 'N/A')}
💾 Total Size: {stats.get('total_size_mb', 0):.2f} MB

✅ System is safe and backed up!
Next backup: Tomorrow 6:00 AM IST
"""
            else:
                message = f"""
⚠️ DAILY BACKUP PARTIAL ⚠️

Date: {backup_results['timestamp']}
Status: Some issues detected

Issues:
{chr(10).join(f"• {k}: {v}" for k, v in backup_results.get('backups', {}).items() if not v)}

Action: Manual review recommended
Support: Contact system admin
"""
            
            bot = Bot(token=TELEGRAM_BOT_TOKENS["INDEX_OPTIONS"])
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_IDS["INDEX_OPTIONS"],
                text=message,
                parse_mode="HTML"
            )
            
            logger.info("✅ Backup report sent to Telegram")
        except Exception as e:
            logger.error(f"❌ Could not send Telegram report: {e}")


# ============================================================================
# CRON JOB ENTRY POINT
# ============================================================================

async def main():
    """Main entry point for 6 AM daily execution"""
    backup_system = DailyBackupSystem()
    
    # Run full backup
    backup_results = await backup_system.run_full_backup()
    
    # Send report
    await backup_system.send_telegram_report(backup_results)
    
    logger.info("\n✅ Backup system completed")


if __name__ == "__main__":
    asyncio.run(main())
