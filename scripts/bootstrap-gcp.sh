#!/bin/bash
# ติดตั้ง Nida Bot บน Cloud Run ใน GCP project ใหม่ด้วยคำสั่งเดียว รันซ้ำได้ ทรัพยากรที่มีอยู่แล้วจะถูกข้าม
#
# ใช้: ./scripts/bootstrap-gcp.sh --project <PROJECT_ID> [ตัวเลือก]
# ตัวเลือกทั้งหมดตั้งในไฟล์ deploy.env ได้ (ดู deploy.env.example) ค่าจาก command line มีผลก่อน
# รันจาก Cloud Shell หรือเครื่องที่มี gcloud, git, openssl และ curl ใน clone ของ repo นี้
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

usage() {
  cat <<'EOF'
ใช้: ./scripts/bootstrap-gcp.sh --project <PROJECT_ID> [ตัวเลือก]

  --project ID          GCP project (บังคับ)
  --region REGION       default: asia-southeast1
  --service NAME        ชื่อ Cloud Run service และคำนำหน้าชื่อทรัพยากร (default: nida-bot)
  --gemini-key KEY      Gemini API key (default: สร้าง key ใหม่ใน project)
  --gemini-model MODEL  model สำหรับแชต (default: gemini-3.5-flash)
  --private             ไม่เปิดให้เข้าถึงแบบไม่ต้อง login (default: เปิดสาธารณะ)
  --dry-run             พิมพ์คำสั่งที่จะรัน โดยไม่สร้างหรือแก้ทรัพยากร
  -h, --help            แสดงข้อความนี้
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --project) need_value "$@"; PROJECT_ID="$2"; shift 2 ;;
    --region) need_value "$@"; REGION="$2"; shift 2 ;;
    --service) need_value "$@"; SERVICE_NAME="$2"; shift 2 ;;
    --gemini-key) need_value "$@"; GEMINI_KEY="$2"; shift 2 ;;
    --gemini-model) need_value "$@"; GEMINI_MODEL="$2"; shift 2 ;;
    --private) PUBLIC=false; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "ไม่รู้จักตัวเลือก $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -n "$PROJECT_ID" ] || { usage >&2; exit 2; }
[[ "$SERVICE_NAME" =~ ^[a-z][a-z0-9-]{0,19}$ ]] ||
  die "--service ต้องเป็นตัวพิมพ์เล็ก ตัวเลข หรือ - ขึ้นต้นด้วยตัวอักษร ยาวไม่เกิน 20 ตัว"
resource_names

# ---------------------------------------------------------------------------
# 1. ตรวจเงื่อนไข
# ---------------------------------------------------------------------------
preflight() {
  step "ตรวจเงื่อนไข"
  local cmd
  for cmd in gcloud git openssl curl; do
    command -v "$cmd" >/dev/null || die "ไม่พบคำสั่ง $cmd"
  done
  [ -f "$REPO_ROOT/Dockerfile" ] && [ -f "$REPO_ROOT/cloudbuild.yaml" ] ||
    die "ไม่พบ Dockerfile หรือ cloudbuild.yaml ต้องรันจาก clone ของ repo นี้"

  local account
  account=$(gcloud config get-value account 2>/dev/null || true)
  [ -n "$account" ] || die "ยังไม่ได้ login gcloud ให้รัน: gcloud auth login"
  info "บัญชี: $account"

  gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1 ||
    die "ไม่พบ project $PROJECT_ID หรือไม่มีสิทธิ์เข้าถึง"

  local billing
  billing=$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingEnabled)' 2>/dev/null || true)
  case "$billing" in
    True) info "billing: เปิดแล้ว" ;;
    False) die "project $PROJECT_ID ยังไม่ได้ผูก billing account" ;;
    *) info "billing: ตรวจไม่ได้ (ข้ามการตรวจ ถ้ายังไม่ได้ผูก billing ขั้นเปิด API จะล้มเหลว)" ;;
  esac

  check_permissions
}

check_permissions() {
  local perms=(
    run.services.create run.services.setIamPolicy artifactregistry.repositories.create
    cloudbuild.builds.create iam.serviceAccounts.create iam.serviceAccounts.actAs
    storage.buckets.create resourcemanager.projects.setIamPolicy
    serviceusage.services.enable secretmanager.secrets.create secretmanager.secrets.setIamPolicy
  )
  [ -n "$GEMINI_KEY" ] || perms+=(apikeys.keys.create)
  # ไม่ตรวจ storage.buckets.setIamPolicy: Owner ได้สิทธิ์นี้กับ bucket ใน project ผ่าน role legacy ของ bucket
  # แต่ตรวจที่ระดับ project จะไม่เจอ

  local body response granted missing=() p
  body=$(printf '"%s",' "${perms[@]}")
  body="{\"permissions\":[${body%,}]}"
  # ส่ง token ผ่าน stdin ไม่ให้ไปอยู่ใน argument ของ process
  response=$(printf 'header = "Authorization: Bearer %s"\n' "$(gcloud auth print-access-token)" |
    curl -fsS -K - -H "Content-Type: application/json" -X POST -d "$body" \
      "https://cloudresourcemanager.googleapis.com/v1/projects/$PROJECT_ID:testIamPermissions" 2>/dev/null) || {
    info "ตรวจสิทธิ์ไม่ได้ ข้ามการตรวจ"
    return 0
  }
  granted=$(tr -d ' \n' <<<"$response")
  for p in "${perms[@]}"; do
    [[ "$granted" == *"\"$p\""* ]] || missing+=("$p")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    die "สิทธิ์ไม่พอ ขาด: ${missing[*]}
ต้องมีบทบาท Owner หรือ Editor + Security Admin + Secret Manager Admin ใน project $PROJECT_ID"
  fi
  info "สิทธิ์: ครบ"
}

# ---------------------------------------------------------------------------
# 2. เปิด API
# ---------------------------------------------------------------------------
enable_apis() {
  step "เปิด API"
  run gc services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
    secretmanager.googleapis.com apikeys.googleapis.com generativelanguage.googleapis.com \
    storage.googleapis.com iam.googleapis.com
}

# ---------------------------------------------------------------------------
# 3. Artifact Registry และ bucket
# ---------------------------------------------------------------------------
setup_registry() {
  step "Artifact Registry $AR_REPO"
  if exists artifacts repositories describe "$AR_REPO" --location "$REGION"; then
    info "มี repository แล้ว"
  else
    run gc artifacts repositories create "$AR_REPO" --location "$REGION" \
      --repository-format docker --description "Nida Bot images"
  fi
}

create_bucket() {
  local bucket="$1"
  if exists storage buckets describe "gs://$bucket"; then
    info "มี gs://$bucket แล้ว"
  else
    run gc storage buckets create "gs://$bucket" --location "$REGION" \
      --uniform-bucket-level-access --public-access-prevention
  fi
}

setup_buckets() {
  step "Cloud Storage"
  local lifecycle
  lifecycle=$(mktemp)
  # ข้อมูลของแอป: chat_history/ (แชตที่กำลังเล่น) ลบเองหลัง 1 วัน, score/ (ผลประเมิน) เก็บถาวร
  create_bucket "$DATA_BUCKET"
  echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":1,"matchesPrefix":["chat_history/"]}}]}' >"$lifecycle"
  run gc storage buckets update "gs://$DATA_BUCKET" --lifecycle-file "$lifecycle" >/dev/null
  # source code ที่ส่งให้ Cloud Build ลบเองหลัง 7 วัน
  create_bucket "$BUILD_BUCKET"
  echo '{"rule":[{"action":{"type":"Delete"},"condition":{"age":7}}]}' >"$lifecycle"
  run gc storage buckets update "gs://$BUILD_BUCKET" --lifecycle-file "$lifecycle" >/dev/null
  rm -f "$lifecycle"
}

# ---------------------------------------------------------------------------
# 4. Gemini API key ใน Secret Manager
# ---------------------------------------------------------------------------
gemini_key() {
  if [ -n "$GEMINI_KEY" ]; then
    printf '%s' "$GEMINI_KEY"
    return
  fi
  local key_name
  key_name=$(gc services api-keys list --filter="displayName=$API_KEY_NAME" --format='value(name)' 2>/dev/null | head -n1)
  if [ -z "$key_name" ]; then
    run gc services api-keys create --display-name "$API_KEY_NAME" \
      --api-target service=generativelanguage.googleapis.com >/dev/null
    [ "$DRY_RUN" = true ] && return
    key_name=$(gc services api-keys list --filter="displayName=$API_KEY_NAME" --format='value(name)' | head -n1)
  fi
  gc services api-keys get-key-string "$key_name" --format='value(keyString)'
}

create_secret() {
  local name="$1" value="$2"
  [ "$DRY_RUN" = true ] || [ -n "$value" ] || die "ค่าของ $name ว่าง ไม่สร้าง secret"
  run_with_stdin "$value" gc secrets create "$name" --replication-policy automatic --data-file=-
}

# สร้าง secret ที่เป็นค่าสุ่ม ถ้าสร้างใหม่จะเก็บค่าไว้ในตัวแปร $3 เพื่อแสดงตอนจบ
random_secret() {
  local name="$1" value="$2" var="$3"
  if exists secrets describe "$name"; then
    info "มี $name แล้ว"
    return
  fi
  create_secret "$name" "$value"
  [ -z "$var" ] || printf -v "$var" '%s' "$value"
}

setup_secrets() {
  step "Secret Manager"
  ADMIN_PASSWORD_CREATED="" USER_PASSWORD_CREATED="" AI_PASSWORD_CREATED=""

  if exists secrets describe "$SECRET_API_KEY"; then
    info "มี $SECRET_API_KEY แล้ว"
    [ -z "$GEMINI_KEY" ] || info "ไม่เขียนทับ key เดิม ถ้าต้องการเปลี่ยนให้ใช้ scripts/rotate-secret.sh"
  else
    [ -n "$GEMINI_KEY" ] || info "สร้าง Gemini API key ($API_KEY_NAME) จำกัดเฉพาะ generativelanguage.googleapis.com"
    local api_key
    api_key=$(gemini_key) || die "สร้างหรืออ่าน Gemini API key ไม่สำเร็จ"
    create_secret "$SECRET_API_KEY" "$api_key"
  fi

  # key สำหรับเซ็น cookie login และรหัสผ่านในเว็บ สุ่มใหม่ รหัสผ่านแสดงครั้งเดียวตอนจบ
  random_secret "$SECRET_SECRET_KEY" "$(openssl rand -hex 32)" ""
  random_secret "$SECRET_ADMIN_PASSWORD" "$(random_password)" ADMIN_PASSWORD_CREATED
  random_secret "$SECRET_USER_PASSWORD" "$(random_password)" USER_PASSWORD_CREATED
  random_secret "$SECRET_AI_PASSWORD" "$(random_password)" AI_PASSWORD_CREATED
}

# ---------------------------------------------------------------------------
# 5. service account สำหรับรันแอปและสำหรับ build
# ---------------------------------------------------------------------------
create_sa() {
  local name="$1" email="$2" display="$3"
  if exists iam service-accounts describe "$email"; then
    info "มี $email แล้ว"
  else
    run gc iam service-accounts create "$name" --display-name "$display"
  fi
}

setup_service_accounts() {
  step "service account"
  create_sa "$RUN_SA_NAME" "$RUN_SA_EMAIL" "Nida Bot Cloud Run"
  create_sa "$BUILD_SA_NAME" "$BUILD_SA_EMAIL" "Nida Bot Cloud Build"

  # แอป: อ่าน secret ของระบบ และอ่านเขียนข้อมูลใน bucket ของตัวเองเท่านั้น
  local s
  for s in "${ALL_SECRETS[@]}"; do
    retry run gc secrets add-iam-policy-binding "$s" \
      --member "serviceAccount:$RUN_SA_EMAIL" --role roles/secretmanager.secretAccessor >/dev/null ||
      die "ให้สิทธิ์อ่าน $s ไม่สำเร็จ"
  done
  retry run gc storage buckets add-iam-policy-binding "gs://$DATA_BUCKET" \
    --member "serviceAccount:$RUN_SA_EMAIL" --role roles/storage.objectUser >/dev/null ||
    die "ให้สิทธิ์ใช้ bucket ข้อมูลไม่สำเร็จ"
  info "$RUN_SA_NAME: อ่าน secret ของระบบและใช้ gs://$DATA_BUCKET"

  # build: อ่าน source, push image เข้า repository นี้ และเขียน log
  retry run gc storage buckets add-iam-policy-binding "gs://$BUILD_BUCKET" \
    --member "serviceAccount:$BUILD_SA_EMAIL" --role roles/storage.objectViewer >/dev/null ||
    die "ให้สิทธิ์อ่าน bucket build ไม่สำเร็จ"
  retry run gc artifacts repositories add-iam-policy-binding "$AR_REPO" --location "$REGION" \
    --member "serviceAccount:$BUILD_SA_EMAIL" --role roles/artifactregistry.writer >/dev/null ||
    die "ให้สิทธิ์ push image ไม่สำเร็จ"
  retry run gc projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$BUILD_SA_EMAIL" --role roles/logging.logWriter --condition None >/dev/null ||
    die "ให้สิทธิ์ logging.logWriter ไม่สำเร็จ"
  info "$BUILD_SA_NAME: อ่าน source, push image และเขียน log"
}

# ---------------------------------------------------------------------------
# 6. build และ deploy
# ---------------------------------------------------------------------------
deploy() {
  # ส่งค่าที่อาจตั้งจาก command line ต่อให้ deploy.sh (ซึ่งอ่าน deploy.env ซ้ำ)
  local args=(--project "$PROJECT_ID" --region "$REGION" --service "$SERVICE_NAME" --gemini-model "$GEMINI_MODEL")
  [ "$PUBLIC" = true ] || args+=(--private)
  [ "$DRY_RUN" = false ] || args+=(--dry-run)
  "$REPO_ROOT/scripts/deploy.sh" "${args[@]}"
}

show_password() {
  local label="$1" value="$2" secret="$3"
  if [ "$DRY_RUN" = true ]; then
    info "$label: (สุ่มใหม่และแสดงที่นี่ ถ้ายังไม่มี secret $secret)"
  elif [ -n "$value" ]; then
    info "$label: $value (แสดงครั้งเดียว อ่านภายหลังได้จาก secret $secret)"
  else
    info "$label: ใช้รหัสเดิมใน secret $secret"
  fi
}

main() {
  [ "$DRY_RUN" = false ] || echo "โหมด dry-run: ไม่สร้างหรือแก้ทรัพยากรใดๆ"
  preflight
  enable_apis
  setup_registry
  setup_buckets
  setup_secrets
  setup_service_accounts
  deploy
  step "บัญชีในเว็บ"
  show_password "admin (username: admin)" "$ADMIN_PASSWORD_CREATED" "$SECRET_ADMIN_PASSWORD"
  show_password "user (username: user)" "$USER_PASSWORD_CREATED" "$SECRET_USER_PASSWORD"
  show_password "รหัสเปิด AI" "$AI_PASSWORD_CREATED" "$SECRET_AI_PASSWORD"
  cat <<EOF

  ก่อนใช้งานจริง ต้องมีเครดิต Gemini API ของ project $PROJECT_ID
  ตั้งค่าและเติมเครดิตที่ https://aistudio.google.com/billing

  รับโค้ดเวอร์ชันใหม่: ./scripts/deploy.sh --project $PROJECT_ID
EOF
}

main
