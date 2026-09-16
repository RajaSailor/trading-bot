import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
DEPLOYMENT_DIR = REPO_ROOT / "deployment"
BACKUPS_DIR = REPO_ROOT / "backups"


class DeploymentScriptsTests(unittest.TestCase):
    def _make_fake_docker(self, temp_dir: Path) -> tuple[Path, Path]:
        log_path = temp_dir / "docker_calls.log"
        fake_bin_dir = temp_dir / "bin"
        fake_bin_dir.mkdir(parents=True, exist_ok=True)
        docker_path = fake_bin_dir / "docker"
        docker_path.write_text(
            "#!/bin/sh\n"
            "echo \"$@\" >> \"$DOCKER_LOG\"\n"
            "cat >/dev/null || true\n"
            "exit \"${DOCKER_EXIT_CODE:-0}\"\n"
        )
        docker_path.chmod(0o755)
        return fake_bin_dir, log_path

    def _base_env(self, fake_bin_dir: Path, log_path: Path) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin_dir}:{env.get('PATH', '')}"
        env["DOCKER_LOG"] = str(log_path)
        env["POSTGRES_USER"] = "trading_bot"
        env["POSTGRES_DB"] = "trading_bot"
        return env

    def test_backup_success_creates_sql_file(self):
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        before_files = set(BACKUPS_DIR.glob("postgres_*.sql"))

        with tempfile.TemporaryDirectory() as tmp:
            fake_bin_dir, log_path = self._make_fake_docker(Path(tmp))
            env = self._base_env(fake_bin_dir, log_path)
            result = subprocess.run(
                ["bash", str(DEPLOYMENT_DIR / "backup.sh")],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        after_files = set(BACKUPS_DIR.glob("postgres_*.sql"))
        new_files = after_files - before_files
        self.assertTrue(new_files)
        for file_path in new_files:
            file_path.unlink(missing_ok=True)

    def test_backup_failure_cleans_tmp_file(self):
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        before_tmp = set(BACKUPS_DIR.glob("postgres_*.sql.tmp"))

        with tempfile.TemporaryDirectory() as tmp:
            fake_bin_dir, log_path = self._make_fake_docker(Path(tmp))
            env = self._base_env(fake_bin_dir, log_path)
            env["DOCKER_EXIT_CODE"] = "1"
            result = subprocess.run(
                ["bash", str(DEPLOYMENT_DIR / "backup.sh")],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(0, result.returncode)
        after_tmp = set(BACKUPS_DIR.glob("postgres_*.sql.tmp"))
        self.assertEqual(before_tmp, after_tmp)

    def test_restore_missing_file_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin_dir, log_path = self._make_fake_docker(Path(tmp))
            env = self._base_env(fake_bin_dir, log_path)
            result = subprocess.run(
                ["bash", str(DEPLOYMENT_DIR / "restore.sh"), "does-not-exist.sql"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Backup file not found", result.stdout)

    def test_restore_resets_schema_before_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            fake_bin_dir, log_path = self._make_fake_docker(temp_dir)
            env = self._base_env(fake_bin_dir, log_path)
            backup_file = temp_dir / "sample.sql"
            backup_file.write_text("SELECT 1;\n")

            result = subprocess.run(
                ["bash", str(DEPLOYMENT_DIR / "restore.sh"), str(backup_file)],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            calls = log_path.read_text().splitlines()

        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("DROP SCHEMA IF EXISTS public CASCADE", calls[0])
        self.assertIn("compose exec -T postgres psql", calls[1])


if __name__ == "__main__":
    unittest.main()
