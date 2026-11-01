#!/bin/bash
# ลบทรัพยากรทั้งหมดที่ scripts/bootstrap-gcp.sh สร้างไว้ใน project
# ใช้: ./scripts/destroy-gcp.sh --project <PROJECT_ID> [--region REGION] [--service NAME] [--dry-run]
#
# ต้องพิมพ์ชื่อ project ยืนยันก่อนลบ และจะถามแยกว่าจะลบ bucket ข้อมูล (ประวัติแชตและคะแนน) ด้วยหรือไม่
# การลบย้อนกลับไม่ได้
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --project) need_value "$@"; PROJECT_ID="$2"; shift 2 ;;
    --region) need_value "$@"; REGION="$2"; shift 2 ;;
    --service) need_value "$@"; SERVICE_NAME="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "ไม่รู้จักตัวเลือก $1" >&2; usage >&2; exit 2 ;;
  esac
done
[ -n "$PROJECT_ID" ] || { usage >&2; exit 2; }
resource_names
command -v gcloud >/dev/null || die "ไม่พบคำสั่ง gcloud"

FAILED=()

# ลบเมื่อมีทรัพยากรอยู่ ถ้าลบไม่สำเร็จจะจดไว้แล้วทำขั้นต่อไป
# ใช้: delete <ชื่อที่แสดง> <อาร์กิวเมนต์ของ describe...> -- <อาร์กิวเมนต์ของ delete...>
delete() {
  local label="$1" describe=() remove=()
  shift
  while [ "$1" != "--" ]; do describe+=("$1"); shift; done
  shift
  remove=("$@")
  if ! exists "${describe[@]}"; then
    info "ไม่มี $label"
    return 0
  fi
  if run gc "${remove[@]}" >/dev/null; then
    [ "$DRY_RUN" = true ] || info "ลบ $label แล้ว"
  else
    info "ลบ $label ไม่สำเร็จ"
    FAILED+=("$label")
  fi
}

delete_bucket() {
  local bucket="$1"
  if ! exists storage buckets describe "gs://$bucket"; then
    info "ไม่มี gs://$bucket"
    return 0
  fi
  if run gc storage rm --recursive "gs://$bucket" >/dev/null; then
    [ "$DRY_RUN" = true ] || info "ลบ gs://$bucket แล้ว"
  else
    FAILED+=("bucket gs://$bucket")
  fi
}

ask() {
  local answer
  read -r -p "$1 " answer
  printf '%s' "$answer"
}

# ---------------------------------------------------------------------------
# ยืนยัน
# ---------------------------------------------------------------------------
cat <<EOF
จะลบระบบ Nida Bot ใน project $PROJECT_ID:
  Cloud Run service $SERVICE_NAME, Artifact Registry $AR_REPO (image ทั้งหมด),
  bucket gs://$BUILD_BUCKET, service account $RUN_SA_NAME และ $BUILD_SA_NAME,
  secret ${#ALL_SECRETS[@]} ตัว (${ALL_SECRETS[*]}) และ Gemini API key ที่ bootstrap สร้าง
EOF

DELETE_DATA=false
if [ "$DRY_RUN" = true ]; then
  echo "โหมด dry-run: แสดงคำสั่งโดยไม่ลบจริง (รวมคำสั่งลบ bucket ข้อมูล)"
  DELETE_DATA=true
else
  [ -t 0 ] || die "ต้องรันในเทอร์มินัลเพื่อยืนยันการลบ"
  [ "$(ask "พิมพ์ชื่อ project ($PROJECT_ID) เพื่อยืนยัน:")" = "$PROJECT_ID" ] || die "ชื่อ project ไม่ตรง ยกเลิก"
  case "$(ask "ลบ bucket ข้อมูล gs://$DATA_BUCKET (ประวัติแชตและคะแนน) ด้วยหรือไม่ [y/N]:")" in
    y | Y) DELETE_DATA=true ;;
  esac
fi

# ---------------------------------------------------------------------------
# ลบ
# ---------------------------------------------------------------------------
step "Cloud Run และ image"
delete "Cloud Run service $SERVICE_NAME" run services describe "$SERVICE_NAME" --region "$REGION" -- \
  run services delete "$SERVICE_NAME" --region "$REGION"
delete "Artifact Registry $AR_REPO" artifacts repositories describe "$AR_REPO" --location "$REGION" -- \
  artifacts repositories delete "$AR_REPO" --location "$REGION"
delete_bucket "$BUILD_BUCKET"

step "service account"
if exists iam service-accounts describe "$BUILD_SA_EMAIL"; then
  run gc projects remove-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$BUILD_SA_EMAIL" \
    --role roles/logging.logWriter --condition None >/dev/null || info "ถอนสิทธิ์ logging.logWriter ไม่สำเร็จ ข้ามไปลบ service account"
fi
delete "service account $RUN_SA_NAME" iam service-accounts describe "$RUN_SA_EMAIL" -- iam service-accounts delete "$RUN_SA_EMAIL"
delete "service account $BUILD_SA_NAME" iam service-accounts describe "$BUILD_SA_EMAIL" -- iam service-accounts delete "$BUILD_SA_EMAIL"

step "secret และ API key"
for s in "${ALL_SECRETS[@]}"; do
  delete "secret $s" secrets describe "$s" -- secrets delete "$s"
done
key_names=$(gc services api-keys list --filter="displayName=$API_KEY_NAME" --format='value(name)' 2>/dev/null || true)
if [ -z "$key_names" ]; then
  info "ไม่มี API key $API_KEY_NAME"
fi
for k in $key_names; do
  if run gc services api-keys delete "$k" >/dev/null; then
    [ "$DRY_RUN" = true ] || info "ลบ API key $API_KEY_NAME แล้ว"
  else
    FAILED+=("API key $k")
  fi
done

step "ข้อมูล"
if [ "$DELETE_DATA" = true ]; then
  delete_bucket "$DATA_BUCKET"
else
  info "เก็บ gs://$DATA_BUCKET ไว้ (มีค่าใช้จ่ายค่าพื้นที่เก็บ ลบเองได้ภายหลัง)"
fi

step "สรุป"
if [ "$DRY_RUN" = true ]; then
  info "dry-run: ไม่ได้ลบอะไร"
elif [ ${#FAILED[@]} -eq 0 ]; then
  info "ลบเรียบร้อย"
else
  info "ลบไม่สำเร็จ: ${FAILED[*]}"
  info "ตรวจสาเหตุแล้วรันสคริปต์นี้ใหม่ได้ ทรัพยากรที่ลบแล้วจะถูกข้าม"
  exit 1
fi
