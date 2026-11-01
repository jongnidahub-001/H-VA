#!/bin/bash
# เปลี่ยนค่า secret ใน Secret Manager แล้วสร้าง revision ใหม่ของ Cloud Run ให้ใช้ค่าใหม่
# ใช้: ./scripts/rotate-secret.sh <ชื่อ secret> --project <PROJECT_ID> [--region REGION] [--service NAME] [--no-apply]
#
# ชื่อ secret (ใส่หรือไม่ใส่ชื่อ service นำหน้าก็ได้)
#   google-api-key    Gemini API key (ถามค่าใหม่แบบไม่แสดงบนจอ)
#   admin-password    รหัสผ่านบัญชี admin (สุ่มใหม่)
#   user-password     รหัสผ่านบัญชี user (สุ่มใหม่)
#   ai-password       รหัสเปิดฟีเจอร์ AI ในหน้าแชต (สุ่มใหม่)
#   secret-key        key สำหรับเซ็น cookie login สุ่มใหม่ ทุกคนต้อง login ใหม่
#
# --no-apply  เปลี่ยนค่าอย่างเดียว ค่าใหม่มีผลเมื่อ deploy ครั้งถัดไป (scripts/deploy.sh)
set -euo pipefail
# shellcheck source=lib/common.sh
. "$(dirname "$0")/lib/common.sh"

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }

NAME="" APPLY=true
while [ $# -gt 0 ]; do
  case "$1" in
    --project) need_value "$@"; PROJECT_ID="$2"; shift 2 ;;
    --region) need_value "$@"; REGION="$2"; shift 2 ;;
    --service) need_value "$@"; SERVICE_NAME="$2"; shift 2 ;;
    --no-apply) APPLY=false; shift ;;
    -h | --help) usage; exit 0 ;;
    -*) echo "ไม่รู้จักตัวเลือก $1" >&2; usage >&2; exit 2 ;;
    *) [ -z "$NAME" ] || { usage >&2; exit 2; }; NAME="$1"; shift ;;
  esac
done
[ -n "$NAME" ] || { usage >&2; exit 2; }
[ -n "$PROJECT_ID" ] || die "ต้องระบุ --project (หรือ PROJECT_ID ใน deploy.env)"
resource_names

SECRET="$SERVICE_NAME-${NAME#"$SERVICE_NAME"-}"
case "$SECRET" in
  "$SECRET_API_KEY" | "$SECRET_SECRET_KEY" | "$SECRET_ADMIN_PASSWORD" | "$SECRET_USER_PASSWORD" | "$SECRET_AI_PASSWORD") ;;
  *) die "ไม่รู้จัก secret $NAME" ;;
esac
exists secrets describe "$SECRET" || die "ไม่พบ secret $SECRET ใน project $PROJECT_ID"

add_version() {
  printf '%s' "$1" | gc secrets versions add "$SECRET" --data-file=- >/dev/null
  info "เพิ่ม version ใหม่ของ $SECRET แล้ว"
}

step "เปลี่ยน $SECRET"
if [ "$SECRET" = "$SECRET_API_KEY" ]; then
  read -r -s -p "  Gemini API key ใหม่: " value
  echo
  [ -n "$value" ] || die "ไม่ได้ใส่ค่า"
  add_version "$value"
elif [ "$SECRET" = "$SECRET_SECRET_KEY" ]; then
  add_version "$(openssl rand -hex 32)"
  info "หลัง revision ใหม่ทำงาน ทุกคนต้อง login ใหม่"
else
  value=$(random_password)
  add_version "$value"
  info "รหัสใหม่: $value"
fi

if [ "$APPLY" = false ]; then
  info "ค่าใหม่จะมีผลเมื่อรัน scripts/deploy.sh ครั้งถัดไป"
  exit 0
fi

# Cloud Run อ่าน secret ตอน instance เริ่ม จึงต้องสร้าง revision ใหม่
step "สร้าง revision ใหม่ของ $SERVICE_NAME"
gc run services update "$SERVICE_NAME" --region "$REGION" \
  --update-env-vars "SECRET_ROTATED_AT=$(date +%Y%m%d%H%M%S)" >/dev/null
info "เสร็จแล้ว"
[ "$SECRET" != "$SECRET_API_KEY" ] || info "ถ้า key เดิมรั่วไหล ให้ลบ key เดิมที่ APIs & Services > Credentials"
