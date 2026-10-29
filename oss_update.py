#!/usr/bin/env python3
"""把一张 Let's Encrypt 证书（通常是覆盖多个域名的 SAN 证书）绑定到各 OSS Bucket 的自定义域名。

域名与 Bucket 通过重复的 --domain / --bucket 参数成对传入，按出现顺序一一对应：

python oss_update.py \
  --domain oss.jianglu.xyz    --bucket jiangluxyz \
  --domain assets.jianglu.xyz --bucket jiangluxyz-assets \
  --domain static.jianglu.xyz --bucket jiangluxyz-static \
  --endpoint oss-cn-hangzhou.aliyuncs.com --region cn-hangzhou \
  --access-key-id <AK> --access-key-secret <SK> \
  --private-key "$(cat privkey.pem)" --certificate "$(cat fullchain.pem)"

单个域名更新失败不会影响其它域名，最后汇总并以非 0 退出码表示有失败。
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional

import oss2
from oss2.models import CertInfo, CnameInfo, PutBucketCnameRequest

# OSS 返回的证书有效期格式，如 "Apr  1 12:00:00 2027 GMT"
OSS_DATE_FORMAT = "%b %d %H:%M:%S %Y GMT"


def parse_oss_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, OSS_DATE_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def certificate_not_after(pem: str) -> Optional[datetime]:
    """解析本地证书的到期时间，用于判断 OSS 上的证书是否已经是最新。

    未安装 cryptography 或解析失败时返回 None（此时按“需要更新”处理）。
    """
    try:
        from cryptography import x509
    except ImportError:
        return None

    try:
        cert = x509.load_pem_x509_certificate(pem.encode())
    except Exception:
        return None

    not_after = getattr(cert, "not_valid_after_utc", None)
    if not_after is not None:
        return not_after
    return cert.not_valid_after.replace(tzinfo=timezone.utc)


class OSS:
    def __init__(self, access_key_id: str, access_key_secret: str, endpoint: str, bucket_name: str,
                 region: str = "cn-hangzhou"):
        self.auth = oss2.AuthV4(access_key_id, access_key_secret)
        self.endpoint = endpoint
        self.bucket_name = bucket_name
        self.region = region
        self.bucket = oss2.Bucket(self.auth, self.endpoint, bucket_name=self.bucket_name, region=self.region)

    def run_update(self, target_cname: str, private_key: str, certificate: str) -> bool:
        """更新单个域名的证书。返回 True 表示实际更新，False 表示已是最新跳过。"""
        cname_info = self._get_matched_cname(target_cname)
        if not cname_info:
            raise RuntimeError(f"Bucket {self.bucket_name} 中未找到自定义域名 {target_cname}")
        return self._update_cname(cname_info, private_key, certificate)

    def _get_matched_cname(self, target_cname: str) -> Optional[CnameInfo]:
        for c in self._get_cname_info():
            if c.domain == target_cname:
                return c
        return None

    def _get_cname_info(self, isprint: bool = True) -> list:
        list_result = self.bucket.list_bucket_cname()
        if isprint:
            print(f"Bucket {self.bucket_name} 已绑定 {len(list_result.cname)} 个自定义域名：")
            for c in list_result.cname:
                print(f"  证书 ID： {getattr(c.certificate, 'cert_id', None)}")
                print(f"  自定义域名： {c.domain}")
                print(f"  最后修改时间： {c.last_modified}")
        return list_result.cname

    def _update_cname(self, cname_info: CnameInfo, private_key: str, certificate: str) -> bool:
        now = datetime.now(timezone.utc)
        local_not_after = certificate_not_after(certificate)
        if local_not_after is not None and local_not_after <= now:
            raise RuntimeError(f"待绑定证书已过期：{local_not_after.isoformat()}")

        existing = cname_info.certificate
        oss_not_after = parse_oss_datetime(getattr(existing, "valid_end_date", None)) if existing else None

        # OSS 上已有同一张证书且尚未过期时无需重复绑定（本地重复执行时可直接跳过）。
        if existing and local_not_after and oss_not_after and oss_not_after == local_not_after and oss_not_after > now:
            print(f"  证书已是最新（到期时间：{oss_not_after.isoformat()}），跳过")
            return False

        if existing and oss_not_after and oss_not_after > now:  # 已绑定证书且未过期：原地更新
            print("  证书未过期，将更新证书。当前证书信息如下：")
            print(f"  {vars(existing)}")
            cert = CertInfo(
                previous_cert_id=existing.cert_id,
                certificate=certificate,
                private_key=private_key,
                force=True,
            )
        else:  # 没有绑定证书，或旧证书已过期：直接创建新证书
            print("  证书已过期或未绑定证书，将创建新的证书")
            cert = CertInfo(certificate=certificate, private_key=private_key, force=True)

        self.bucket.put_bucket_cname(PutBucketCnameRequest(cname_info.domain, cert))
        print("  已更新")
        return True


def parse_args():
    parser = argparse.ArgumentParser(description="Aliyun OSS 多域名证书自动更新脚本")
    parser.add_argument(
        '--domain', action='append', required=True, metavar='DOMAIN',
        help='自定义域名，可重复传入；与 --bucket 按出现顺序一一对应（如 DOMAIN_1 配 BUCKET_NAME_1）',
    )
    parser.add_argument(
        '--bucket', action='append', required=True, metavar='BUCKET',
        help='OSS Bucket 名称，可重复传入；与 --domain 按出现顺序一一对应',
    )
    parser.add_argument('--access-key-id', required=True, help='阿里云 AccessKeyId')
    parser.add_argument('--access-key-secret', required=True, help='阿里云 AccessKeySecret')
    parser.add_argument('--endpoint', required=True, help='OSS Endpoint，例如 oss-cn-hangzhou.aliyuncs.com')
    parser.add_argument('--region', default='cn-hangzhou', help='OSS 区域，默认 cn-hangzhou')
    parser.add_argument('--private-key', required=True, help='证书私钥内容')
    parser.add_argument('--certificate', required=True, help='证书内容（fullchain）')

    args = parser.parse_args()
    if len(args.domain) != len(args.bucket):
        parser.error(f"--domain 与 --bucket 数量必须一致（{len(args.domain)} != {len(args.bucket)}）")
    return args


def main() -> int:
    args = parse_args()
    targets = list(zip(args.domain, args.bucket))

    if len({domain for domain, _ in targets}) != len(targets):
        print("--domain 中存在重复域名，请检查 secrets 配置", file=sys.stderr)
        return 1
    for domain, bucket in targets:
        if not domain.strip() or not bucket.strip():
            print("--domain / --bucket 不能为空，请检查 secrets 是否配置", file=sys.stderr)
            return 1

    print(f"共 {len(targets)} 个域名待处理：")
    for domain, bucket in targets:
        print(f"  {domain} -> {bucket}")

    updated, skipped, failures = [], [], []
    for domain, bucket in targets:
        print(f"\n[{domain}] Bucket：{bucket}，Endpoint：{args.endpoint}，Region：{args.region}")
        try:
            client = OSS(
                access_key_id=args.access_key_id,
                access_key_secret=args.access_key_secret,
                endpoint=args.endpoint,
                bucket_name=bucket,
                region=args.region,
            )
            if client.run_update(domain, args.private_key, args.certificate):
                updated.append(domain)
            else:
                skipped.append(domain)
        except Exception as exc:  # 单个域名失败不影响其它域名
            failures.append(domain)
            print(f"[{domain}] 更新失败：{exc}", file=sys.stderr)

    print("\n===== 结果汇总 =====")
    print(f"更新成功：{len(updated)} {updated}")
    print(f"已是最新：{len(skipped)} {skipped}")
    print(f"失败：{len(failures)} {failures}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
