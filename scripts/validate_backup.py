#!/usr/bin/env python3
"""Extract only the four expected files from a Nexus-panel backup."""
import sys
import tarfile
from pathlib import Path

archive, destination = map(Path, sys.argv[1:])
names = {'panel.sql', 'env.snapshot', 'domain.txt', 'manifest.txt'}
try:
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        if {entry.name for entry in members} != names or len(members) != 4:
            raise ValueError('备份文件清单无效')
        for entry in members:
            if not entry.isfile() or entry.size < 1 or entry.size > 1024**3:
                raise ValueError('备份内容无效或过大')
            source = bundle.extractfile(entry)
            if source is None:
                raise ValueError('备份内容不可读取')
            with (destination / entry.name).open('xb') as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
        if (destination / 'manifest.txt').read_text().strip() != 'NEXUS_PANEL_BACKUP_V1':
            raise ValueError('备份版本无效')
except (OSError, tarfile.TarError, ValueError) as error:
    raise SystemExit(f'备份校验失败：{error}')
