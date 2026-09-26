#!/usr/bin/env python3
"""Render the initial database seed without shipping shared login credentials."""
import hashlib
import os
import re
import sys
from pathlib import Path

source, destination = map(Path, sys.argv[1:])
username = os.environ['NEXUS_ADMIN_USER']
password = os.environ['NEXUS_ADMIN_PASSWORD']
backend = os.environ['NEXUS_BACKEND_ADDRESS']
if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', username) or len(password) < 12:
    raise SystemExit('管理员账号或密码无效')
if not re.fullmatch(r'[A-Za-z0-9.:-]+', backend) or len(backend) > 180:
    raise SystemExit('节点后端地址无效')
data = source.read_text()
data = data.replace('__NEXUS_ADMIN_USER__', username)
data = data.replace('__NEXUS_ADMIN_MD5__', hashlib.md5(password.encode()).hexdigest())
data = data.replace('__NEXUS_BACKEND_ADDRESS__', backend)
if '__NEXUS_' in data:
    raise SystemExit('初始化模板尚有未替换字段')
destination.write_text(data)
destination.chmod(0o600)
