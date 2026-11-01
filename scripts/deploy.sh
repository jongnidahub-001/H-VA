#!/bin/bash
# build image จากโค้ดใน clone นี้ด้วย Cloud Build แล้ว deploy เป็น revision ใหม่ของ Cloud Run
# ใช้: ./scripts/deploy.sh --project <PROJECT_ID> [ตัวเลือก]
# ต้องรัน scripts/bootstrap-gcp.sh ใน project นี้มาก่อนอย่างน้อยหนึ่งครั้ง
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

usage() {
  cat <<'EOF'
ใช้: ./scripts/deploy.sh --project <PROJECT_ID> [ตัวเลือก]

  --project ID          GCP project (บังคับ)
  --region REGION       default: asia-southeast1
  --service NAME        ชื่อ Cloud Run service (default: nida-bot)
  --image IMAGE         deploy image ที่มีอยู่แล้ว ไม่ build ใหม่ (ใช้ย้อนกลับไปเวอร์ชันเก่า)
  --gemini-model MODEL  model สำหรับแชต (default: gemini-3.5-flash)
  --private             ไม่เปิดให้เข้าถึงแบบไม่ต้อง login
  --dry-run            พิมพ์คำสั่งที่จะรัน โดยไม่ build หรือ deploy
  -h, --help            แสดงข้อความนี้

ค่าอื่น เช่น GEMINI_MODEL, CPU, MEMORY, MAX_INSTANCES ตั้งใน deploy.env (ดู deploy.env.example)
EOF
}

IMAGE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project) need_value "$@"; PROJECT_ID="$2"; shift 2 ;;
    --region) need_value "$@"; REGION="$2"; shift 2 ;;
    --service) need_value "$@"; SERVICE_NAME="$2"; shift 2 ;;
    --image) need_value "$@"; IMAGE="$2"; shift 2 ;;
    --gemini-model) need_value "$@"; GEMINI_MODEL="$2"; shift 2 ;;
    --private) PUBLIC=false; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "ไม่รู้จักตัวเลือก $1" >&2; usage >&2; exit 2 ;;
  esac
done
[ -n "$PROJECT_ID" ] || { usage >&2; exit 2; }
resource_names
command -v gcloud >/dev/null || die "ไม่พบคำสั่ง gcloud"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# tag = เวลา + commit ถ้ามีไฟล์ที่ยังไม่ commit จะต่อท้ายด้วย -dirty
image_tag() {
  local rev
  rev=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)
  [ -z "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ] || rev="$rev-dirty"
  echo "$(date +%Y%m%d-%H%M%S)-$rev"
}

build_image() {
  step "build image"
  IMAGE="$IMAGE_BASE:$(image_tag)"
  info "$IMAGE"
  run gc builds submit "$REPO_ROOT" --config "$REPO_ROOT/cloudbuild.yaml" \
    --substitutions "_IMAGE=$IMAGE" \
    --service-account "projects/$PROJECT_ID/serviceAccounts/$BUILD_SA_EMAIL" \
    --gcs-source-staging-dir "gs://$BUILD_BUCKET/source" ||
    die "build ไม่สำเร็จ ดู log ได้ที่ https://console.cloud.google.com/cloud-build/builds?project=$PROJECT_ID"
}

# secret ทุกตัวเป็นตัวแปรของแอป อ่าน version ล่าสุดตอน instance เริ่ม
render_secret_env() {
  local pair
  for pair in "${SECRET_ENV[@]}"; do
    cat <<EOF
            - name: ${pair%%=*}
              valueFrom:
                secretKeyRef:
                  name: ${pair#*=}
                  key: latest
EOF
  done
}

# สเปกของ service ทั้งหมดอยู่ที่นี่ deploy ซ้ำจะได้ค่าเดิมเสมอ
# แอปไม่เก็บ state ในเครื่อง (ข้อมูลอยู่ใน gs://$DATA_BUCKET) จึง scale เป็นศูนย์และขยายหลาย instance ได้
render_service() {
  cat <<EOF
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: $SERVICE_NAME
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/minScale: "$MIN_INSTANCES"
        autoscaling.knative.dev/maxScale: "$MAX_INSTANCES"
        run.googleapis.com/startup-cpu-boost: "true"
    spec:
      serviceAccountName: $RUN_SA_EMAIL
      containerConcurrency: $CONCURRENCY
      timeoutSeconds: $REQUEST_TIMEOUT
      containers:
        - image: $IMAGE
          ports:
            - name: http1
              containerPort: 8080
          env:
$(render_secret_env)
            - name: DATA_BUCKET
              value: "$DATA_BUCKET"
            - name: GEMINI_MODEL
              value: "$GEMINI_MODEL"
            - name: GEMINI_EVAL_MODEL
              value: "$GEMINI_EVAL_MODEL"
            - name: WEB_CONCURRENCY
              value: "$WORKERS"
          resources:
            limits:
              cpu: "$CPU"
              memory: $MEMORY
          startupProbe:
            httpGet:
              path: /login
            periodSeconds: 5
            failureThreshold: 24
EOF
}

deploy_service() {
  step "deploy Cloud Run service $SERVICE_NAME"
  local spec="$TMP_DIR/service.yaml"
  render_service >"$spec"
  run gc run services replace "$spec" --region "$REGION" >/dev/null ||
    die "deploy ไม่สำเร็จ ดู log ด้วย: gcloud run services logs read $SERVICE_NAME --region $REGION --project $PROJECT_ID"

  if [ "$PUBLIC" = true ]; then
    run gc run services add-iam-policy-binding "$SERVICE_NAME" --region "$REGION" \
      --member allUsers --role roles/run.invoker >/dev/null ||
      info "เปิดให้เข้าถึงแบบไม่ต้อง login ไม่ได้ (org policy อาจห้าม allUsers) ผู้ใช้ต้องมีสิทธิ์ roles/run.invoker"
  else
    run gc run services remove-iam-policy-binding "$SERVICE_NAME" --region "$REGION" \
      --member allUsers --role roles/run.invoker >/dev/null 2>&1 || true
  fi
}

check_health() {
  step "ตรวจระบบ"
  if [ "$DRY_RUN" = true ]; then
    info "[dry-run] ตรวจว่า URL ของ service ตอบ 200"
    return
  fi
  URL=$(service_url)
  [ -n "$URL" ] || die "หา URL ของ service ไม่ได้"
  if [ "$PUBLIC" != true ]; then
    info "service ไม่เปิดสาธารณะ ข้ามการตรวจ"
    return
  fi
  local i
  for i in 1 2 3 4 5 6; do
    # /login เปิดได้โดยไม่ต้อง login, /api/domains ต้องตอบ 401 เมื่อยังไม่ login
    if curl -fsS -o /dev/null --max-time 30 "$URL/login" &&
      [ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$URL/api/domains")" = 401 ]; then
      info "ระบบพร้อมแล้ว"
      return
    fi
    sleep 10
  done
  die "$URL ยังไม่ตอบ ดู log ด้วย: gcloud run services logs read $SERVICE_NAME --region $REGION --project $PROJECT_ID"
}

main() {
  [ "$DRY_RUN" = false ] || echo "โหมด dry-run: ไม่ build หรือ deploy จริง"
  [ -n "$IMAGE" ] || build_image
  deploy_service
  check_health

  step "เสร็จแล้ว"
  cat <<EOF
  URL:    ${URL:-<url ของ service>}
  Image:  $IMAGE
  Model:  $GEMINI_MODEL (ประเมินผลด้วย $GEMINI_EVAL_MODEL)

  ดู log:
    gcloud run services logs read $SERVICE_NAME --region $REGION --project $PROJECT_ID
  ย้อนกลับไป image เดิม:
    ./scripts/deploy.sh --project $PROJECT_ID --image <IMAGE เดิม>
EOF
}

main
