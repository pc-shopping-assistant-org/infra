# gearPC Infrastructure

Một repo, một compose. Infra trên server; application chạy local hoặc dùng image
GHCR qua profile `apps`. Caddy global vẫn do server quản lý.

| Chế độ | Chạy gì |
| --- | --- |
| Mặc định | PostgreSQL, Redis, RabbitMQ, Kafka, Debezium Connect, Elasticsearch, LGTM |
| `apps` | Thêm Eureka, Gateway, 8 business services gồm sync, AI và Next.js |
| `setup` | Job đăng ký/cập nhật connector, chạy khi catalog đã migrate |

## Global env — không đóng vào image

`.env.docker` là nguồn cấu hình runtime trên server. File thật được ignore;
`.env.docker.example` là mẫu được commit. `compose.sh` luôn truyền file này vào
Compose, không phụ thuộc `.env` mặc định hoặc file env bên trong image.

Chỉ inject các biến cần thiết cho từng service bằng `environment`; không đẩy cả
global env/signing keys sang FE. Docker context BE chỉ có JAR; AI/FE loại `.env*`.
Không truyền deployment secrets/public config qua build args. Google client ID
được FE đọc qua `/api/config` lúc runtime, cùng `GOOGLE_CLIENT_ID` của identity.

`.release.env` do CD quản lý, chỉ giữ ba image tag BE/AI/FE sau deploy thành công.
File này không chứa secrets, không commit và không sửa `.env.docker`.
Muốn pin tag thủ công, cập nhật/xóa đúng dòng override trong `.release.env`.

## 1. Khởi tạo server

Cần Docker + Compose v2 có `--wait`, Python 3, Bash, flock, rsync và SSH.
SSH user cần quyền Docker và ghi vào `DEPLOY_PATH`; không dùng root nếu không cần.

```sh
test -f .env.docker || cp .env.docker.example .env.docker
chmod 600 .env.docker
# Điền passwords riêng; openssl rand -hex 32 tạo secret phù hợp cả JAAS/URL.
./compose.sh config --quiet
./compose.sh up -d
./compose.sh ps
```

Không in `config` đầy đủ vào log/ticket: output chứa secrets. PostgreSQL 18 mount
`/var/lib/postgresql`; init script tạo database khi volume mới. Flyway của từng
Java service quản lý schema, AI dùng Alembic + native saver bootstrap.

Các port mặc định chỉ bind localhost. Kafka external có SASL/PLAIN, Redis và
Elasticsearch bật auth; transport native được mã hóa qua SSH. Không public các
port plaintext chỉ vì đã có mật khẩu. Single broker/node và LGTM all-in-one là
baseline dev/demo, chưa phải HA hoặc production hardening hoàn chỉnh.

## 2. Dev local dùng infra server

```sh
ssh -N -o ExitOnForwardFailure=yes \
  -L 5433:127.0.0.1:5433 -L 6379:127.0.0.1:6379 \
  -L 5672:127.0.0.1:5672 -L 29092:127.0.0.1:29092 \
  -L 9200:127.0.0.1:9200 -L 4318:127.0.0.1:4318 \
  -L 3300:127.0.0.1:3300 user@your-server
```

Dừng infra local chiếm port trước, không xóa volume. Kafka advertised host mặc định
là `127.0.0.1:29092`: giữ đúng port local này, không chỉ đổi bootstrap port.

- BE: copy `env/backend.env.example` thành `env/backend.env`, điền credentials
  server, import vào IDE/process. Bash/zsh:
  `set -a; source /path/to/infra/env/backend.env; set +a`. Spring không tự đọc env
  file. Giữ SMTP/Cloudinary/payment settings riêng hiện có.
- AI: merge `env/ai.env.example` vào `.env` local, giữ provider/catalog mapping.
  FE vẫn gọi BE/AI local. Eureka, business services và sync đều chạy local.
- Không bật `apps` trên server khi dùng mô hình này; không trộn nhiều worker
  khác phiên bản trên cùng DB/topics/index. Không chạy integration test xóa dữ liệu
  vào DB dùng chung.

Có private network riêng thì đổi `INFRA_BIND_ADDRESS`, `KAFKA_ADVERTISED_HOST`
và env client tương ứng. Các listener này chưa tự cấp TLS cho kết nối Internet.

## 3. CDC

Sau khi catalog-service chạy Flyway thành công:

```sh
./compose.sh --profile setup run --rm register-connector
```

Job inject credentials, cập nhật connector và đợi connector/tasks RUNNING.
Connect giữ config/offset trên Kafka; không reset slot/topic khi chỉ cập nhật config.

`PostgreSQL → Debezium → Kafka → sync-service → catalog API → Elasticsearch`.
Retry/DLT của sync vẫn là ISSUE-141, chưa được sửa bởi infra split.
Kafka retention mặc định 7 ngày, không phải backup.

## 4. Deploy đầu tiên bằng GHCR

Điền phần apps trong `.env.docker`: RSA keys bền vững, DB URL nội bộ
`postgres:5432/ai_db`, catalog UUID mapping thật và provider credentials.
Password trong URL cần percent-encode. RSA keys là Base64 DER PKCS#8/X.509,
không PEM markers. Không generate key tạm cho deployed identity.

```sh
# Package private: docker login ghcr.io bằng PAT có read:packages.
python3 scripts/deploy.py --check
python3 scripts/deploy.py
./compose.sh --profile apps ps
./compose.sh --profile setup run --rm register-connector
```

Không build source trên server. AI image chứa migrations; `ai-migrate` phải thành
công trước AI. Các image phải được publish từ release trước; `release` là tag
bootstrap, CD sau đó pin `sha-<full commit>`.

## 5. GitHub CI/CD release

Cả backend, ai-service, web-frontend và infra đều test trên push/PR/merge queue.
**Chỉ push/manual dispatch trên đúng nhánh `release` mới publish/deploy**;
không main, tag hoặc PR. App image có tag `release` và `sha-<full commit>`.

Mỗi repo tạo GitHub Environment **release**, cho phép deploy branch `release`:

| Loại | Tên | Giá trị |
| --- | --- | --- |
| Variable | `SSH_HOST` | Host/IP server |
| Variable | `SSH_USER` | User deploy có quyền Docker |
| Variable | `SSH_PORT` | Port SSH, mặc định 22 |
| Variable | `DEPLOY_PATH` | Đường dẫn tuyệt đối tới infra, không khoảng trắng |
| Secret | `SSH_PRIVATE_KEY` | Private key của deploy user |
| Secret | `SSH_KNOWN_HOSTS` | Host key đã xác minh ngoài pipeline |

Không gửi private key vào chat, không tắt host-key checking. Với SSH port khác
22, known_hosts cần đúng dạng `[host]:port`. Server login GHCR một lần bằng
credential đọc package; pipeline publish dùng GITHUB_TOKEN, không đưa token đó
vào container. Bật packages write theo policy của org nếu đang bị chặn.

| Repo release | CD thực hiện |
| --- | --- |
| backend | Đợi 10 image thành công, cập nhật nhóm 10 service BE, gồm sync |
| ai-service | Pull AI, dừng executor cũ, migrate, cập nhật AI |
| web-frontend | Chỉ pull/cập nhật FE |
| infra | Upload config, giữ env/volumes, chỉ cập nhật dependency containers |

CD dùng lock chung server, không cancel release đang chạy, bỏ qua run không còn
là branch head. App release dùng `--no-deps`, không restart DB/Caddy hoặc app repo
khác; đợi health trước khi ghi tag vào `.release.env`. Deploy đầu tiên dùng bước 4
để bootstrap dependency graph. Infra release cần `.env.docker` đã có trên server.

Migration/health lỗi thì job fail, không ghi nhận tag thành công. Không tự rollback
schema: kiểm tra migration và phục hồi/cập nhật có chủ đích. Một deploy có thể đã
đổi một phần container trước khi fail; tag history không phải transaction rollback.

## 6. Caddy global

Caddy trên host: upstream FE `127.0.0.1:3000`, API `127.0.0.1:8080`,
Grafana `127.0.0.1:3300`. Thêm domain thật vào Caddyfile global, ví dụ:

```caddyfile
gearpc.example.com {
    reverse_proxy 127.0.0.1:3000
}
api.gearpc.example.com {
    reverse_proxy 127.0.0.1:8080
}
grafana.gearpc.example.com {
    reverse_proxy 127.0.0.1:3300
}
```

Caddy Docker: attach network `pcshop-infra`, dùng `web-frontend:3000`,
`api-gateway:8080`, `lgtm:3000`; localhost trong container không phải host.
Không public Kafka Connect, OTLP hoặc Eureka. Không proxy native DB/Kafka/AMQP bằng
HTTP reverse_proxy. Grafana yêu cầu password đã cấu hình.

## Dữ liệu và verification

Volume names explicit, không đổi theo checkout. Chuyển stack cũ: backup/restore
hoặc chọn volume đã kiểm tra, không attach một volume vào hai container đang chạy.
Đổi env password không tự đổi tài khoản đã tồn tại trong DB/Rabbit/ES/Grafana.

Không dùng `down -v`. Backup trước migration/deploy; retention không thay backup.
`compose/` cũ giữ để tham khảo monolith, không dùng làm entrypoint mới.
Chưa tạo remote GitHub cho repo infra, chưa cấu hình Environment/secrets trên GitHub.

`python3 -m unittest discover -s tests -v` kiểm tra profile/auth/endpoints và scoped
release bằng fake Docker. Không start container, không chứng minh server runtime.
