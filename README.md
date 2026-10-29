# AliyunOSS_CertUpdate

自动化更新阿里云 OSS 自定义域名证书的解决方案。

## 项目简介
本项目通过 GitHub Actions 自动申请一张覆盖多个域名的 Let's Encrypt 证书（SAN 多域名证书），
并把同一张证书自动绑定到各自 OSS Bucket 的自定义域名，实现证书自动续期和更新。

## 主要文件说明
- `oss_update.py`：核心脚本，按成对传入的 `--domain` / `--bucket` 映射，把证书逐个绑定到各 Bucket 的自定义域名。
- `alidns.sh`：用于 certbot DNS-01 验证的钩子脚本，自动增删 `_acme-challenge` TXT 记录。
- `.github/workflows/alioss-update-auto.yml`：GitHub Actions 工作流，自动化申请证书并更新 OSS 证书。

## 阿里云 ACCESS KEY 权限说明（偷懒版）
在 阿里云 [RAM 访问控制](https://ram.console.aliyun.com/users)中新建用户，并添加权限
 - AliyunOSSFullAccess 管理对象存储服务（OSS）权限
 - AliyunYundunCertFullAccess 管理云盾证书服务的权限
 - AliyunDNSFullAccess 管理云解析（DNS）的权限

## 使用方法
### 1. 配置 Secrets
在 GitHub 仓库的 Settings > Secrets and variables > Actions 中添加以下密钥：

| Secret | 说明 |
| --- | --- |
| `ACCESS_KEY_ID` | 阿里云 AccessKeyId |
| `ACCESS_KEY_SECRET` | 阿里云 AccessKeySecret |
| `ENDPOINT` | OSS Endpoint，例如 `oss-cn-hangzhou.aliyuncs.com` |
| `REGION` | 阿里云区域（如 `cn-hangzhou`） |
| `EMAIL` | 用于申请证书的邮箱 |
| `DOMAIN_1` / `DOMAIN_2` / `DOMAIN_3` | 需要绑定证书的自定义域名 |
| `BUCKET_NAME_1` / `BUCKET_NAME_2` / `BUCKET_NAME_3` | 与域名编号一一对应的 OSS Bucket |

> 编号按顺序一一配对：`DOMAIN_1` ↔ `BUCKET_NAME_1`、`DOMAIN_2` ↔ `BUCKET_NAME_2`、`DOMAIN_3` ↔ `BUCKET_NAME_3`。
> 顺序本身不重要，只要同一编号下是「同一个域名 + 它的 Bucket」即可。
> 三个 Bucket 需在同一区域，共用 `ENDPOINT` / `REGION`。

### 2. 域名数量变更
工作流目前固定读取 3 组（`DOMAIN_1~3` / `BUCKET_NAME_1~3`）。增删域名时：
1. 在仓库里增删对应的 Secrets；
2. 同步修改 `.github/workflows/alioss-update-auto.yml` 中 job 的 `env`，以及两个步骤里的 `DOMAINS` / `BUCKETS` 数组。

### 3. 工作流自动执行
每月 2 日 20:00 UTC（北京时间 3 日 04:00）自动执行，也可在 Actions 页面手动触发。流程如下：
1. 读取 `DOMAIN_1~3`，用 certbot 一次申请一张覆盖所有域名的 SAN 证书，保存于 `/etc/letsencrypt/live/<CERT_NAME>/`（`CERT_NAME` 在工作流 env 中，默认 `jianglu-oss`）。
2. 通过 `alidns.sh` 完成每个域名的 DNS-01 验证。
3. 读取 `fullchain.pem` 和 `privkey.pem`，按 `DOMAIN_N ↔ BUCKET_NAME_N` 逐个绑定到各自 Bucket 的自定义域名。

certbot 默认只在证书到期前 30 天内才续期；OSS 上若已绑定同一张证书，脚本会自动跳过，不会重复上传。

### 4. 手动运行脚本
如需本地手动运行：

```bash
pip install -r requirements.txt
python oss_update.py \
  --domain oss.jianglu.xyz    --bucket jiangluxyz \
  --domain assets.jianglu.xyz --bucket jiangluxyz-assets \
  --domain static.jianglu.xyz --bucket jiangluxyz-static \
  --access-key-id <你的AccessKeyId> \
  --access-key-secret <你的AccessKeySecret> \
  --endpoint <你的Endpoint> \
  --region <你的Region> \
  --private-key "$(sudo cat /etc/letsencrypt/live/jianglu-oss/privkey.pem)" \
  --certificate "$(sudo cat /etc/letsencrypt/live/jianglu-oss/fullchain.pem)"
```

## 注意事项
- 首次新增域名时，要先在 OSS 控制台为对应 Bucket 绑定自定义域名并配置 CNAME 解析；本脚本只负责更新该域名的证书，不会自动创建 CNAME 绑定。
- 新增或删除域名后，修改 Secrets 和工作流中的域名/Bucket 数组，并手动触发一次 workflow；certbot 已加 `--expand`，非交互模式也能扩展证书域名。
- GitHub Actions 的每个 runner 都是全新环境，`/etc/letsencrypt` 不会跨次保留，因此每次运行都会重新签发一张证书。每月 1 次远低于 Let's Encrypt 的限流（同一域名集合每周最多 5 张），可以正常使用；如果想减少签发次数，可以自行用 `actions/cache` 持久化 certbot 配置目录。
- GitHub Actions 的 cron 使用 UTC 时间，`TZ` 环境变量对 cron 不生效。
- GitHub 会在仓库长期（约 60 天）没有提交时自动停用定时 workflow，建议偶尔手动触发一次，避免证书续期中断。
- 证书和私钥文件需有读取权限；阿里云账号需有 OSS 管理权限。
- 不要高频使用强制续期（`--force-renewal`），以免触发 Let's Encrypt 的证书签发限流。

## 常见问题

### Actions 更新 OSS 证书时报 `AttributeError: module 'lib' has no attribute 'GEN_EMAIL'`

原因：`oss2 → aliyunsdkcore（内置 urllib3）` 会 import `pyOpenSSL`，而 `aliyunsdkcore` 依赖 `cryptography`。
GitHub runner 通过 apt 安装 certbot 时会带入系统旧版 `pyOpenSSL`（Ubuntu 24.04 为 23.2.0），它只兼容 `cryptography < 42`；
pip 安装新版 `cryptography` 后两者混用，就会报上面的错。

`requirements.txt` 已显式安装新版 `pyOpenSSL`（`pyOpenSSL>=24`），让 pip 把 `pyOpenSSL + cryptography` 作为一对管理；
用户级 site-packages 优先于系统 dist-packages，不会再加载系统旧版 pyOpenSSL。
工作流里也加了 `Verify Python dependencies` 步骤，用于在签发证书前提前发现这类依赖问题。

如果本地环境混装过旧版本，重装依赖即可：

```bash
pip uninstall -y pyOpenSSL cryptography
pip install -r requirements.txt
```

## 致谢
感谢 [hui-shao/AliyunOSS_Cert_Update](https://github.com/hui-shao/AliyunOSS_Cert_Update) 提供的 OSS 证书上传教程。
感谢 [justjavac/certbot-dns-aliyun](https://github.com/justjavac/certbot-dns-aliyun) 的证书申请教程。

## License
MIT
