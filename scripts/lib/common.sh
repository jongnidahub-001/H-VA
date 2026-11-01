#!/bin/bash
# ค่าตั้งต้น ชื่อทรัพยากร และ helper ที่ใช้ร่วมกันใน scripts/*.sh
# ใช้ด้วย: . "$(dirname "$0")/lib/common.sh"

# ให้ error ใน $(...) หยุดสคริปต์ด้วย (bash 4.4 ขึ้นไป บน bash ของ macOS จะข้าม)
shopt -s inherit_errexit 2>/dev/null || true

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# ค่าตั้งต้น ถูกแทนด้วย deploy.env แล้วตามด้วย command line ของแต่ละสคริปต์
# shellcheck disable=SC2034
{
  PROJECT_ID="" REGION="asia-southeast1" SERVICE_NAME="nida-bot" GEMINI_KEY=""
  GEMINI_MODEL="gemini-3.5-flash" GEMINI_EVAL_MODEL="gemini-3.5-flash"
  CPU="1" MEMORY="512Mi" WORKERS="1" CONCURRENCY="40" MIN_INSTANCES="0" MAX_INSTANCES="2"
  REQUEST_TIMEOUT="300" PUBLIC=true DRY_RUN=false
}

if [ -f "$REPO_ROOT/deploy.env" ]; then
  # shellcheck disable=SC1091
  . "$REPO_ROOT/deploy.env"
fi

# ชื่อทรัพยากร เรียกหลังอ่าน command line แล้ว เพราะขึ้นกับ PROJECT_ID, REGION และ SERVICE_NAME
# shellcheck disable=SC2034
resource_names() {
  RUN_SA_NAME="$SERVICE_NAME-run"
  RUN_SA_EMAIL="$RUN_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
  BUILD_SA_NAME="$SERVICE_NAME-build"
  BUILD_SA_EMAIL="$BUILD_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"
  AR_REPO="$SERVICE_NAME"
  IMAGE_BASE="$REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/$SERVICE_NAME"
  DATA_BUCKET="$PROJECT_ID-$SERVICE_NAME-data"
  BUILD_BUCKET="$PROJECT_ID-$SERVICE_NAME-build"
  API_KEY_NAME="$SERVICE_NAME-gemini"
  SECRET_API_KEY="$SERVICE_NAME-google-api-key"
  SECRET_SECRET_KEY="$SERVICE_NAME-secret-key"
  SECRET_ADMIN_PASSWORD="$SERVICE_NAME-admin-password"
  SECRET_USER_PASSWORD="$SERVICE_NAME-user-password"
  SECRET_AI_PASSWORD="$SERVICE_NAME-ai-password"
  ALL_SECRETS=("$SECRET_API_KEY" "$SECRET_SECRET_KEY" "$SECRET_ADMIN_PASSWORD" "$SECRET_USER_PASSWORD" "$SECRET_AI_PASSWORD")
  # ชื่อ secret และชื่อตัวแปรในแอปที่รับค่า (ใช้ตอน deploy)
  SECRET_ENV=("GOOGLE_API_KEY=$SECRET_API_KEY" "SECRET_KEY=$SECRET_SECRET_KEY" "ADMIN_PASSWORD=$SECRET_ADMIN_PASSWORD"
    "USER_PASSWORD=$SECRET_USER_PASSWORD" "AI_PASSWORD=$SECRET_AI_PASSWORD")
}

random_password() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-20; }

need_value() {
  [ $# -ge 2 ] && [ -n "$2" ] || { echo "ตัวเลือก $1 ต้องมีค่า" >&2; exit 2; }
}

step() { printf '\n== %s ==\n' "$*"; }
info() { printf '  %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

gc() { gcloud --project "$PROJECT_ID" --quiet "$@"; }

exists() { gc "$@" >/dev/null 2>&1; }

# คำสั่งสำหรับแสดงใน dry-run แปลง gc เป็นคำสั่ง gcloud เต็ม
show_cmd() {
  [ "$1" = gc ] && { shift; set -- gcloud --project "$PROJECT_ID" "$@"; }
  printf ' %q' "$@"
  printf '\n'
}

# รันคำสั่งที่สร้าง แก้ หรือลบทรัพยากร ใน dry-run จะพิมพ์คำสั่งแทน (ออก stderr จะได้ไม่ปนกับค่าที่ capture)
run() {
  if [ "$DRY_RUN" = true ]; then
    {
      printf '  [dry-run]'
      show_cmd "$@"
    } >&2
  else
    "$@"
  fi
}

# เหมือน run แต่ส่งค่าลับทาง stdin ของคำสั่ง ใน dry-run จะไม่พิมพ์ค่า
run_with_stdin() {
  local value="$1"
  shift
  if [ "$DRY_RUN" = true ]; then
    {
      printf '  [dry-run] <ค่าลับ> |'
      show_cmd "$@"
    } >&2
  else
    printf '%s' "$value" | "$@"
  fi
}

# ทำซ้ำสำหรับ IAM ที่เพิ่งสร้าง (มีช่วง propagate สั้นๆ)
retry() {
  local i
  for i in 1 2 3 4 5 6; do
    "$@" && return 0
    [ "$DRY_RUN" = true ] && return 0
    info "ยังไม่สำเร็จ ลองใหม่ใน 10 วินาที ($i/6)"
    sleep 10
  done
  return 1
}

service_url() {
  gc run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)' 2>/dev/null || true
}
