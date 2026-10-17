# gearPC Infrastructure

Một repo, một compose. Infra trên server; application chạy local hoặc dùng image
GHCR qua profile `apps`. Caddy global vẫn do server quản lý.

```text
compose.yaml / compose.sh     Cấu hình duy nhất và lệnh chạy
.env.docker.example          Mẫu global env; file thật .env.docker nằm ngoài Git
config/                     PostgreSQL, Debezium, Grafana
env/                        Env mẫu cho application chạy local
scripts/                    Bootstrap apps, đăng ký CDC, deploy release
tests/                      Kiểm tra offline cấu hình và release
.github/workflows/          CI/CD nhánh release
```

| Chế độ | Chạy gì |
| --- | --- |
| Mặc định | PostgreSQL, Redis, RabbitMQ, Kafka, Debezium Connect, Elasticsearch, LGTM |
| `apps` | Thêm Eureka, Gateway, 8 business services gồm sync, AI và Next.js |
| `setup` | Job seed catalog và đăng ký/cập nhật connector |

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
`postgres:5432/ai_db` và provider credentials.
Password trong URL cần percent-encode. RSA keys là Base64 DER PKCS#8/X.509,
không PEM markers. Không generate key tạm cho deployed identity.

Trước khi public: identity còn migration tạo admin với credential dev cố định
(ISSUE-142). Cần bootstrap admin bằng secret riêng và xử lý tài khoản cũ; seed
dev này không phải cấu hình production an toàn.

```sh
# Package private: docker login ghcr.io bằng PAT có read:packages.
./scripts/deploy.sh --check
./scripts/deploy-release.sh 1.0.0
./compose.sh --profile apps ps
./compose.sh --profile setup run --rm register-connector
```

Setup kiểm tra image version trên registry, pull, dựng dependencies, gọi `seed-prod`
để migrate + UPSERT taxonomy, rồi chạy backend, migrate AI và chạy AI/FE.
Seed chỉ gồm 12 loại linh kiện/phụ kiện, không có sản phẩm demo.
Chạy lại giữ UUID, tên, trạng thái, hierarchy và mô tả đã có. AI đọc `hardwareType`
trực tiếp từ snapshot catalog; không cần điền category UUID vào env.

Chạy seed riêng: `./scripts/seed-prod.sh`. Job chỉ cần PostgreSQL, không khởi động
web/Kafka listeners của catalog. Infra release cũng chạy seed trước khi restart
services. Image backend phải chứa lệnh `--seed-prod` trước khi dùng setup mới.

Không build source trên server. AI image chứa migrations; `ai-migrate` phải thành
công trước AI. Các image phải được publish từ nhánh release của từng app trước
khi chạy infra release.

## 5. GitHub CI/CD release

Cả backend, ai-service, web-frontend và infra đều test trên push/PR/merge queue.
Nhánh `release/vX.Y.Z` của app **chỉ build/publish GHCR**, không SSH hoặc deploy.
Nhánh cùng tên của infra **mới deploy**. Main, tag và PR không deploy.
Ví dụ app `release/v1.0.0` publish `:1.0.0` + `:sha-<full commit>`;
infra `release/v1.0.0` chọn `:1.0.0` cho cả BE/AI/FE. Cũng hỗ trợ `v1.1.0-rc.1`.

Chỉ repo **infra** cần GitHub Environment **release**. Cấu hình deployment branch
rule cho phép `release/*` (thay rule cũ chỉ cho phép `release`):

| Loại | Tên | Giá trị |
| --- | --- | --- |
| Variable | `SSH_HOST` | Host/IP server |
| Variable | `SSH_USER` | User deploy có quyền Docker |
| Variable | `SSH_PORT` | Port SSH, mặc định 22 |
| Variable | `DEPLOY_PATH` | Đường dẫn tuyệt đối tới infra, không khoảng trắng |
| Secret | `SSH_PRIVATE_KEY` | Private key của deploy user |
| Secret | `SSH_KNOWN_HOSTS` | Host key đã xác minh ngoài pipeline |

Không gửi private key vào chat, không tắt host-key checking. Với SSH port khác
22, known_hosts cần đúng dạng `[host]:port`. Package public không cần server login
GHCR; package private cần credential đọc package. Pipeline publish dùng
GITHUB_TOKEN, không đưa token đó vào container.

| Repo release | Thực hiện |
| --- | --- |
| backend | Test + publish 10 image riêng, gồm sync; không deploy |
| ai-service | Test + publish AI image; không deploy |
| web-frontend | Test + publish FE image; không deploy |
| infra | Upload config, pull image, dependencies → seed → backend → stop AI → migrate → AI/FE |

Đợi tất cả app builds thành công rồi mới push/dispatch nhánh infra cùng version.
Không fallback `latest`/`release`. Dispatch cho phép chọn version riêng qua
`backend_image_tag`, `ai_image_tag`, `web_image_tag`: `X.Y.Z`, `X.Y.Z-rc.N` hoặc
`sha-<full commit>`. Để trống lấy version từ nhánh infra, không từ env cũ.

Không sửa code trên branch version đã phát hành; dùng `release/v1.0.1` cho bản sửa.
GHCR tags chưa được khóa ghi đè tự động (ISSUE-143); đây là quy ước release hiện tại,
không phải bảo đảm image bất biến ở registry.

CD dùng lock server, không cancel release đang chạy, bỏ qua run không còn là
branch head; đợi health rồi ghi ba tag vào `.release.env`. Không sửa `.env.docker`,
không xóa volumes hoặc động đến Caddy global. Server cần `.env.docker` sẵn có.
Chạy thủ công: `./scripts/deploy-release.sh 1.0.0`.
Chọn riêng từng app: `./scripts/deploy-release.sh 1.0.0 1.0.1 1.0.0`.

Script kiểm tra manifest đủ 12 image app rồi pull trước khi đổi container. Thiếu
version, lỗi auth/network hoặc pull lỗi thì dừng, không tự đổi sang version khác.
Chỉ sau khi deploy healthy mới xóa image ID cũ của app vừa thay thế trên server.
Không `--force`, không prune toàn server, không xóa GHCR/volumes. Image còn được
container tham chiếu sẽ giữ lại; cleanup lỗi chỉ cảnh báo, không hủy deploy thành công.
Rollback cần pull lại image cũ từ GHCR; migration vẫn phải tương thích.

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
Repo GitHub: `pc-shopping-assistant-org/infra`. Owner cần cấu hình Environment
`release`, variables/secrets và server env trước khi chạy CD.

`python3 -m unittest discover -s tests -v` kiểm tra profile/auth/endpoints và scoped
release bằng fake Docker. Không start container, không chứng minh server runtime.
