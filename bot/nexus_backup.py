"""
NEXUS Auto-Backup System
─────────────────────────
Backs up all brain data to GitHub automatically.
Free. Unlimited. Restores on any machine.

Setup (one time):
1. Create free account: https://github.com
2. Create private repo: nexus-brain-backup
3. Create personal access token:
   GitHub → Settings → Developer Settings
   → Personal Access Tokens → Tokens (classic)
   → Generate new token → check 'repo' scope
   → Copy token

Add to .env:
  GITHUB_TOKEN=ghp_your_token_here
  GITHUB_REPO=yourusername/nexus-brain-backup

Backup runs every 30 minutes automatically.
"""

import asyncio
import json
import os
import base64
from datetime import datetime
from loguru import logger

BACKUP_FILES = [
    "data/nexus_memory.json",
    "data/strategy_brain.json",
    "data/brain/thoughts.json",
    "data/brain/agents.json",
    "data/brain/goals.json",
    "data/risk_state.json",
    "data/survival_state.json",
    "data/nexus_agents.json",
    "data/internet_brain.json",
    "data/paper_trades.json",
]


async def backup_to_github() -> dict:
    """
    Upload all brain data to GitHub.
    Creates/updates files in your private repo.
    """
    import httpx
    from config.settings import settings

    token = os.getenv("GITHUB_TOKEN", "")
    repo  = os.getenv("GITHUB_REPO", "")

    if not token or not repo:
        logger.warning("Backup: GITHUB_TOKEN or GITHUB_REPO not set in .env")
        return {"success": False, "reason": "No GitHub credentials"}

    headers = {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github.v3+json",
        "Content-Type":  "application/json",
    }

    backed_up = 0
    failed    = 0

    async with httpx.AsyncClient(timeout=15) as client:
        for file_path in BACKUP_FILES:
            if not os.path.exists(file_path):
                continue

            try:
                # Read file content
                content = open(file_path, encoding="utf-8").read()
                encoded = base64.b64encode(content.encode()).decode()

                # GitHub API path
                gh_path = f"backup/{file_path}"
                url     = f"https://api.github.com/repos/{repo}/contents/{gh_path}"

                # Check if file exists (need SHA to update)
                sha = None
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    sha = r.json().get("sha")

                # Create or update
                payload = {
                    "message": f"Auto-backup {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    "content": encoded,
                }
                if sha:
                    payload["sha"] = sha

                r = await client.put(url, headers=headers,
                                    content=json.dumps(payload))

                if r.status_code in [200, 201]:
                    backed_up += 1
                else:
                    logger.warning(f"Backup failed {file_path}: {r.status_code}")
                    failed += 1

            except Exception as e:
                logger.warning(f"Backup error {file_path}: {e}")
                failed += 1

    if backed_up > 0:
        logger.success(f"✅ Backup: {backed_up} files → github.com/{repo}")
    return {"success": backed_up > 0, "backed_up": backed_up, "failed": failed}


async def restore_from_github() -> dict:
    """
    Restore all brain data from GitHub.
    Run this after VPS reset or new deployment.
    """
    import httpx

    token = os.getenv("GITHUB_TOKEN", "")
    repo  = os.getenv("GITHUB_REPO", "")

    if not token or not repo:
        return {"success": False, "reason": "No GitHub credentials"}

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    restored = 0
    failed   = 0

    async with httpx.AsyncClient(timeout=15) as client:
        for file_path in BACKUP_FILES:
            try:
                gh_path = f"backup/{file_path}"
                url     = f"https://api.github.com/repos/{repo}/contents/{gh_path}"

                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    continue

                # Decode content
                content = base64.b64decode(
                    r.json()["content"].replace("\n", "")
                ).decode("utf-8")

                # Create directory if needed
                os.makedirs(os.path.dirname(file_path), exist_ok=True)

                # Write file
                open(file_path, "w", encoding="utf-8").write(content)
                restored += 1
                logger.success(f"✅ Restored: {file_path}")

            except Exception as e:
                logger.warning(f"Restore error {file_path}: {e}")
                failed += 1

    logger.success(f"🔄 Restore complete: {restored} files restored")
    return {"success": restored > 0, "restored": restored, "failed": failed}


async def backup_to_local(backup_dir: str = "backup") -> dict:
    """
    Local backup — copies all data files to backup folder.
    Fallback when GitHub not configured.
    """
    import shutil

    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    backed_up = 0

    for file_path in BACKUP_FILES:
        if not os.path.exists(file_path):
            continue
        try:
            dest_dir = os.path.join(backup_dir, os.path.dirname(file_path))
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copy2(file_path, dest_dir)
            backed_up += 1
        except Exception as e:
            logger.warning(f"Local backup error {file_path}: {e}")

    # Also save a timestamped snapshot
    snapshot_dir = os.path.join(backup_dir, f"snapshot_{timestamp}")
    os.makedirs(snapshot_dir, exist_ok=True)
    for file_path in BACKUP_FILES:
        if os.path.exists(file_path):
            try:
                dest = os.path.join(snapshot_dir, file_path.replace("/","_"))
                shutil.copy2(file_path, dest)
            except Exception:
                pass

    logger.info(f"💾 Local backup: {backed_up} files → {backup_dir}/")
    return {"success": True, "backed_up": backed_up}


async def run_backup_cycle() -> dict:
    """
    Full backup cycle:
    1. Try GitHub first (cloud backup)
    2. Always do local backup (safety net)
    """
    results = {}

    # GitHub backup
    github_result = await backup_to_github()
    results["github"] = github_result

    # Always do local backup too
    local_result = await backup_to_local()
    results["local"] = local_result

    if github_result.get("success"):
        logger.success(
            f"☁️ Cloud backup: {github_result.get('backed_up',0)} files to GitHub"
        )
    else:
        logger.info("💾 Local backup only (add GITHUB_TOKEN for cloud backup)")

    return results


if __name__ == "__main__":
    import sys

    async def main():
        if len(sys.argv) > 1 and sys.argv[1] == "restore":
            print("\n🔄 Restoring from GitHub...")
            result = await restore_from_github()
            print(f"Restored: {result.get('restored', 0)} files")
        else:
            print("\n☁️ Running backup...")
            result = await run_backup_cycle()
            print(f"GitHub: {result['github']}")
            print(f"Local:  {result['local']}")

    asyncio.run(main())
