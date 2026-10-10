"""Discover only NVIDIA credentials in explicitly selected local folders."""
import argparse
import ast
import os
import re
from pathlib import Path
from dotenv import dotenv_values, set_key

TOKEN = re.compile(r'(?<![\w-])nvapi-[A-Za-z0-9_-]{20,}(?![\w-])')

def extract(path):
    try:
        if path.is_symlink() or path.stat().st_size > 262144:
            return []
        raw = path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return []
    if path.suffix.lower() == '.py':
        # Never execute existing project code to obtain a credential.
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            return []
        values = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    values.extend(TOKEN.findall(value.value))
        return values
    return TOKEN.findall(raw)

def discover(folders):
    found = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        # Direct files only; no unrelated folders, browser profiles or backups.
        for path in sorted(folder.iterdir()):
            name = path.name.lower()
            if not path.is_file():
                continue
            is_source = name in ('main.py', 'pararadar_hizli.py')
            is_key_file = (name.startswith('.env') or any(w in name for w in ('nvidia', 'anahtar', 'key'))) and path.suffix.lower() in ('', '.txt', '.json', '.env')
            if is_source or is_key_file:
                found.update(extract(path))
    return found

def configure(target, folders):
    existing = dotenv_values(target, encoding='utf-8-sig').get('NVIDIA_API_KEY')
    if existing and TOKEN.fullmatch(existing):
        print('Mevcut yerel NVIDIA anahtarı korundu; ilk video API erişimini test edecek.')
        return True
    inherited = os.environ.get('NVIDIA_API_KEY', '')
    keys = {inherited} if TOKEN.fullmatch(inherited) else discover(folders)
    if len(keys) != 1:
        print('NVIDIA anahtarı bulunamadı.' if not keys else 'Birden fazla NVIDIA anahtarı bulundu; güvenli seçim yapılamadı.')
        print('Görevler başlatılmadı. NVIDIA_API_KEY değerini yerel pararadar_service/.env dosyasında belirleyin.')
        return False
    set_key(str(target), 'NVIDIA_API_KEY', keys.pop(), quote_mode='always', encoding='utf-8-sig')
    print('NVIDIA anahtarı yerel .env dosyasına kaydedildi; değeri gizlendi. İlk video API erişimini test edecek.')
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--folder', action='append', default=[], type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if configure(args.env_file, args.folder) else 1)
