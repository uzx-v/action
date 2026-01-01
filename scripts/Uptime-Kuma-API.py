#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
from io import BytesIO
from huggingface_hub import HfApi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hf_token", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--github_repo", required=True)
    parser.add_argument("--github_token", required=True)
    parser.add_argument("--hf_space_name", default="uk")
    parser.add_argument("--github_branch", default="main")
    parser.add_argument("--backup_hour", default="4")
    parser.add_argument("--keep_backups", default="5")
    parser.add_argument("--backup_pass", default="")
    parser.add_argument("--cf_tunnel_token", default="")
    
    args = parser.parse_args()
    api = HfApi(token=args.hf_token)
    
    try:
        user_id = api.whoami()["name"]
    except Exception:
        print("❌ 认证失败")
        sys.exit(1)
    
    repo_id = f"{user_id}/{args.hf_space_name}"
    
    # 删除已存在的空间
    try:
        api.repo_info(repo_id=repo_id, repo_type="space")
        api.delete_repo(repo_id=repo_id, repo_type="space")
    except Exception:
        pass
    
    # 配置 Secrets
    space_secrets = [
        {"key": "GITHUB_TOKEN", "value": args.github_token},
        {"key": "GITHUB_REPO", "value": args.github_repo},
        {"key": "GITHUB_BRANCH", "value": args.github_branch},
        {"key": "BACKUP_HOUR", "value": args.backup_hour},
        {"key": "KEEP_BACKUPS", "value": args.keep_backups},
    ]
    
    if args.backup_pass:
        space_secrets.append({"key": "BACKUP_PASS", "value": args.backup_pass})
    if args.cf_tunnel_token:
        space_secrets.append({"key": "CF_TUNNEL_TOKEN", "value": args.cf_tunnel_token})
    
    # 创建空间
    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="space",
            space_sdk="docker",
            private=False,
            space_secrets=space_secrets,
        )
    except Exception:
        print("❌ 创建失败")
        sys.exit(1)
    
    # 上传文件
    readme = f"""---
title: {args.hf_space_name}
emoji: ⚡
colorFrom: red
colorTo: yellow
sdk: docker
pinned: false
---
""".strip()
    
    dockerfile = f"FROM {args.image}"
    
    try:
        for name, content in [("README.md", readme), ("Dockerfile", dockerfile)]:
            api.upload_file(
                repo_id=repo_id,
                path_in_repo=name,
                path_or_fileobj=BytesIO(content.encode("utf-8")),
                repo_type="space",
            )
    except Exception:
        print("❌ 上传失败")
        sys.exit(1)
    
    print("✅ 完成")


if __name__ == "__main__":
    main()
