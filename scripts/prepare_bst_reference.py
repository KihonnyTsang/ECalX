"""Remove private paths from an authorized copy of the frozen 204-point CSV.

This prepares local data, not permission to redistribute it. No network access.
"""
import argparse
import csv
import hashlib
import io
from pathlib import Path

ORIGINAL_SHA256 = 'b75cddb53985f24c8b825e3fbd732cb189e84570007b0bcfbc806a224d72badb'
RELEASE_SHA256 = '92a17a6e4f10487d747c7e316883fbab88c1cdc03e6efdec0e11d4a18851ec89'


def prepare(raw):
    if hashlib.sha256(raw).hexdigest() != ORIGINAL_SHA256:
        raise ValueError('输入不是匹配的冻结204点CSV，未生成数据。')
    rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
    fields = [name for name in rows[0] if name != 'source_pdf_path']
    expected = [{key: row[key] for key in fields} for row in rows]
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(expected)
    clean = output.getvalue().encode('utf-8')
    if len(rows) != 204 or list(csv.DictReader(clean.decode().splitlines())) != expected:
        raise ValueError('记录一致性检查失败。')
    if hashlib.sha256(clean).hexdigest() != RELEASE_SHA256:
        raise ValueError('脱敏副本校验失败。')
    return clean


def main():
    parser = argparse.ArgumentParser(description='将合法取得的冻结BST参考CSV转换为本机使用的脱敏副本。')
    parser.add_argument('original_csv', type=Path)
    parser.add_argument('output_csv', type=Path)
    args = parser.parse_args()
    try:
        clean = prepare(args.original_csv.read_bytes())
        with args.output_csv.open('xb') as output:
            output.write(clean)
    except (OSError, ValueError):
        parser.exit(1, '无法生成参考数据：请核对输入版本、输出目录和文件是否已存在。\n')
    print('204条记录核对一致；已移除私人路径。SHA256：'+RELEASE_SHA256)


if __name__ == '__main__':
    main()
