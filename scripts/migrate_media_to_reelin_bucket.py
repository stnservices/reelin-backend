#!/usr/bin/env python3
"""
Migration script to copy media files from streetfishing bucket to reelin bucket
and update all database URLs.

Usage:
    # Dry run (no changes made):
    python scripts/migrate_media_to_reelin_bucket.py --dry-run

    # Execute migration:
    python scripts/migrate_media_to_reelin_bucket.py

    # Resume from specific offset (if interrupted):
    python scripts/migrate_media_to_reelin_bucket.py --offset 1000

Requirements:
    - boto3
    - asyncpg
    - python-dotenv
"""

import argparse
import asyncio
import os
import sys
from urllib.parse import urlparse
from typing import Optional
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
import asyncpg
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
OLD_BUCKET = "streetfishing"
NEW_BUCKET = "reelin"
OLD_CDN_BASE = "https://streetfishing.fra1.cdn.digitaloceanspaces.com"
NEW_CDN_BASE = "https://reelin.fra1.cdn.digitaloceanspaces.com"
REGION = "fra1"
ENDPOINT_URL = "https://fra1.digitaloceanspaces.com"

# Tables and columns to migrate
TABLES_TO_MIGRATE = [
    ("user_profiles", "profile_picture_url"),
    ("catches", "photo_url"),
    ("catches", "video_url"),
    ("events", "image_url"),
    ("events", "banner_url"),
    ("clubs", "logo_url"),
    ("clubs", "banner_url"),
    ("sponsors", "logo_url"),
    ("competition_rules", "document_url"),
    ("locations", "image_url"),
]


class MediaMigrator:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.s3_client = None
        self.db_pool = None
        self.stats = {
            "files_found": 0,
            "files_copied": 0,
            "files_skipped": 0,
            "files_failed": 0,
            "urls_updated": 0,
            "urls_failed": 0,
        }
        self.failed_files = []
        self.failed_urls = []

    async def init(self):
        """Initialize S3 client and database connection."""
        # Get credentials from environment
        access_key = os.getenv("DO_SPACES_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
        secret_key = os.getenv("DO_SPACES_SECRET") or os.getenv("AWS_SECRET_ACCESS_KEY")
        database_url = os.getenv("DATABASE_URL")

        if not access_key or not secret_key:
            raise ValueError("Missing DO_SPACES_KEY/DO_SPACES_SECRET or AWS credentials")
        if not database_url:
            raise ValueError("Missing DATABASE_URL")

        # Initialize S3 client
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT_URL,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=REGION,
        )

        # Initialize database pool
        # Convert SQLAlchemy URL to asyncpg format
        db_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self.db_pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)

        print(f"[OK] Connected to database and S3")
        print(f"[INFO] Dry run: {self.dry_run}")

    async def close(self):
        """Close database connection."""
        if self.db_pool:
            await self.db_pool.close()

    def extract_key_from_url(self, url: str) -> Optional[str]:
        """Extract S3 key from CDN URL."""
        if not url or OLD_CDN_BASE not in url:
            return None
        return url.replace(f"{OLD_CDN_BASE}/", "")

    def file_exists_in_new_bucket(self, key: str) -> bool:
        """Check if file already exists in new bucket."""
        try:
            self.s3_client.head_object(Bucket=NEW_BUCKET, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    def copy_file(self, key: str) -> bool:
        """Copy file from old bucket to new bucket."""
        try:
            # Copy object
            copy_source = {"Bucket": OLD_BUCKET, "Key": key}
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=NEW_BUCKET,
                Key=key,
                ACL="public-read",
                MetadataDirective="COPY",
            )
            return True
        except ClientError as e:
            print(f"    [ERROR] Failed to copy {key}: {e}")
            return False

    async def get_urls_from_table(self, table: str, column: str) -> list[tuple]:
        """Get all URLs from a table that point to old bucket."""
        async with self.db_pool.acquire() as conn:
            # Check if column exists
            check_query = """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = $1 AND column_name = $2
                )
            """
            exists = await conn.fetchval(check_query, table, column)
            if not exists:
                return []

            query = f"""
                SELECT id, {column} as url
                FROM {table}
                WHERE {column} LIKE $1
            """
            rows = await conn.fetch(query, f"{OLD_CDN_BASE}%")
            return [(row["id"], row["url"]) for row in rows]

    async def update_url_in_table(self, table: str, column: str, record_id: int, new_url: str) -> bool:
        """Update URL in database."""
        if self.dry_run:
            return True
        try:
            async with self.db_pool.acquire() as conn:
                query = f"UPDATE {table} SET {column} = $1 WHERE id = $2"
                await conn.execute(query, new_url, record_id)
            return True
        except Exception as e:
            print(f"    [ERROR] Failed to update {table}.{column} id={record_id}: {e}")
            return False

    async def migrate_table(self, table: str, column: str):
        """Migrate all URLs in a table."""
        print(f"\n[TABLE] {table}.{column}")

        urls = await self.get_urls_from_table(table, column)
        if not urls:
            print(f"  No URLs to migrate")
            return

        print(f"  Found {len(urls)} URLs to migrate")

        for record_id, url in urls:
            self.stats["files_found"] += 1
            key = self.extract_key_from_url(url)

            if not key:
                print(f"  [SKIP] Invalid URL: {url}")
                self.stats["files_skipped"] += 1
                continue

            # Check if already exists in new bucket
            if self.file_exists_in_new_bucket(key):
                print(f"  [EXISTS] {key}")
                self.stats["files_skipped"] += 1
            else:
                # Copy file
                if self.dry_run:
                    print(f"  [DRY-RUN] Would copy: {key}")
                    self.stats["files_copied"] += 1
                else:
                    print(f"  [COPY] {key}")
                    if self.copy_file(key):
                        self.stats["files_copied"] += 1
                    else:
                        self.stats["files_failed"] += 1
                        self.failed_files.append((table, column, record_id, url))
                        continue  # Don't update URL if copy failed

            # Update URL in database
            new_url = url.replace(OLD_CDN_BASE, NEW_CDN_BASE)
            if self.dry_run:
                print(f"  [DRY-RUN] Would update URL: id={record_id}")
                self.stats["urls_updated"] += 1
            else:
                if await self.update_url_in_table(table, column, record_id, new_url):
                    self.stats["urls_updated"] += 1
                else:
                    self.stats["urls_failed"] += 1
                    self.failed_urls.append((table, column, record_id, url))

    async def migrate_all(self):
        """Migrate all tables."""
        print("=" * 60)
        print("MEDIA MIGRATION: streetfishing -> reelin")
        print("=" * 60)
        print(f"Started at: {datetime.now().isoformat()}")

        for table, column in TABLES_TO_MIGRATE:
            await self.migrate_table(table, column)

        print("\n" + "=" * 60)
        print("MIGRATION SUMMARY")
        print("=" * 60)
        print(f"Files found:   {self.stats['files_found']}")
        print(f"Files copied:  {self.stats['files_copied']}")
        print(f"Files skipped: {self.stats['files_skipped']} (already exist)")
        print(f"Files failed:  {self.stats['files_failed']}")
        print(f"URLs updated:  {self.stats['urls_updated']}")
        print(f"URLs failed:   {self.stats['urls_failed']}")

        if self.failed_files:
            print("\n[FAILED FILES]")
            for table, column, record_id, url in self.failed_files:
                print(f"  {table}.{column} id={record_id}: {url}")

        if self.failed_urls:
            print("\n[FAILED URL UPDATES]")
            for table, column, record_id, url in self.failed_urls:
                print(f"  {table}.{column} id={record_id}: {url}")

        if self.dry_run:
            print("\n[DRY RUN] No changes were made. Run without --dry-run to execute.")

        print(f"\nCompleted at: {datetime.now().isoformat()}")


async def main():
    parser = argparse.ArgumentParser(description="Migrate media from streetfishing to reelin bucket")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    args = parser.parse_args()

    migrator = MediaMigrator(dry_run=args.dry_run)

    try:
        await migrator.init()
        await migrator.migrate_all()
    finally:
        await migrator.close()


if __name__ == "__main__":
    asyncio.run(main())
