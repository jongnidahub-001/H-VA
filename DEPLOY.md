# การติดตั้ง Nida Bot บน Google Cloud Run

ระบบรันเป็น container บน Cloud Run เรียก Gemini ผ่าน Gemini API และเก็บข้อมูลเป็นไฟล์ JSON ใน Cloud Storage แอปไม่เก็บ state ในเครื่อง Cloud Run จึงปิด instance เมื่อไม่มีผู้ใช้และเพิ่ม instance เมื่อคนใช้เยอะได้ โดยข้อมูลไม่หาย ค่า API key, รหัสผ่าน และ key สำหรับ login เก็บใน Secret Manager

## บัญชีผู้ใช้

หน้าเว็บมีหน้า login และมีสองบัญชี

| Username | สิทธิ์ |
|---|---|
| `admin` | เข้า admin site ที่ `/admin` อย่างเดียว ไม่มีหน้าแชต |
| `user` | ใช้หน้าแชต |

ฟีเจอร์ AI ในหน้าแชต (คะแนน sentiment, คำแนะนำการตอบ, คำแนะนำด้านอารมณ์) ต้องใส่รหัสเปิด AI แยกอีกชั้น login อยู่ได้ 12 ชั่วโมง

## Admin site

admin site มี 3 หน้า

1. Overview แสดงจำนวนเคสที่ประเมินแล้ว จำนวน agent และจำนวนเคสที่เปิดและไม่เปิด AI พร้อมปุ่มดาวน์โหลดรายงาน ZIP (Excel คะแนนและ Word สรุปแชต) แยกตามการใช้ AI
2. Chat History แสดงทุกเคสที่ประเมินแล้ว ค้นหาตามชื่อ agent หรือสถานการณ์ กรองตามการใช้ AI และช่วงวันที่ได้ กดที่แถวเพื่อดูบทสนทนา คะแนน และความเห็นของเคสนั้น export รายเคสเป็น Word หรือ text และ export หลายเคสเป็น Excel ได้ (sheet Cases หนึ่งแถวต่อเคส และ sheet Messages หนึ่งแถวต่อข้อความ)
3. Settings เปลี่ยน Gemini API key (ทดสอบ key ก่อนบันทึกได้) เลือก model สำหรับแชตและสำหรับประเมินผลจากรายชื่อ model ที่ key นั้นใช้ได้ และเปลี่ยนรหัสเปิด AI

ค่าที่ admin ตั้งในหน้า Settings จะเก็บใน `config/settings.json` ของ bucket ข้อมูล และใช้แทนค่าใน Secret Manager ทันทีโดยไม่ต้อง deploy ใหม่ แต่ละ instance อ่านค่าใหม่ภายใน 30 วินาที ถ้ากด "Use deployment key" หรือ "Use deployment password" ระบบจะกลับไปใช้ค่าใน Secret Manager bucket นี้เป็น private และมีเพียง service account ของแอปที่อ่านได้

แชตที่ยังไม่ได้ปิดเคสจะไม่แสดงใน Chat History และจะถูกลบหลัง 1 วัน

ทุก API ยกเว้น login ต้องผ่านการ login ก่อน และ API กลุ่ม `/api/admin/` ใช้ได้เฉพาะบัญชี admin โดยตรวจที่ server

## สิ่งที่ต้องมี

1. GCP project ที่ผูก billing แล้ว และบัญชีที่มีบทบาท Owner (หรือ Editor + Security Admin + Secret Manager Admin)
2. Cloud Shell หรือเครื่องที่มี `gcloud`, `git`, `openssl` และ `curl` โดย login ด้วย `gcloud auth login` แล้ว
3. เครดิต Gemini API ของ project ตั้งค่าที่ https://aistudio.google.com/billing

## ติดตั้งครั้งแรก

```bash
./scripts/bootstrap-gcp.sh --project <PROJECT_ID>
```

สคริปต์จะเปิด API ที่ต้องใช้ สร้าง Artifact Registry, bucket, Gemini API key, secret และ service account แล้ว build image ด้วย Cloud Build และ deploy ขึ้น Cloud Run เมื่อเสร็จจะแสดง URL รหัสผ่านของบัญชี admin และ user และรหัสเปิด AI ที่สุ่มให้ (แสดงครั้งเดียว อ่านภายหลังได้จาก Secret Manager เช่น `gcloud secrets versions access latest --secret nida-bot-admin-password --project <PROJECT_ID>`) รันซ้ำได้ ทรัพยากรที่มีอยู่แล้วจะถูกข้าม

ถ้ามี Gemini API key อยู่แล้วให้ใส่ `--gemini-key <KEY>` ถ้าต้องการให้เฉพาะคนที่มีบัญชี Google ที่ได้รับสิทธิ์เปิดหน้าเว็บได้ ให้ใส่ `--private` และดูตัวเลือกทั้งหมดด้วย `--help` หรือใช้ `--dry-run` เพื่อดูคำสั่งที่จะรันโดยไม่สร้างทรัพยากร

ค่าที่ใช้บ่อยตั้งไว้ในไฟล์ `deploy.env` ได้ โดยคัดลอกจาก `deploy.env.example` แล้วแก้ค่า สคริปต์ทุกตัวจะอ่านไฟล์นี้ให้เอง

## ทรัพยากรที่สร้าง

ชื่อทรัพยากรขึ้นต้นด้วยชื่อ service (ค่าตั้งต้น `nida-bot`)

| ทรัพยากร | ชื่อ | หน้าที่ |
|---|---|---|
| Cloud Run service | `nida-bot` | รันแอป |
| Artifact Registry | `nida-bot` | เก็บ image |
| Bucket | `<PROJECT_ID>-nida-bot-data` | `chat_history/` แชตที่กำลังเล่น ลบเองหลัง 1 วัน, `score/` ผลประเมินและบทสนทนาทุกเคส เก็บถาวร, `config/` ค่าที่ admin ตั้ง |
| Bucket | `<PROJECT_ID>-nida-bot-build` | source ที่ส่งให้ Cloud Build ลบเองหลัง 7 วัน |
| Secret | `nida-bot-google-api-key` | Gemini API key ตั้งต้น (admin เปลี่ยนแทนได้ในหน้า Settings) |
| Secret | `nida-bot-admin-password`, `nida-bot-user-password` | รหัสผ่านของบัญชี admin และ user |
| Secret | `nida-bot-ai-password` | รหัสเปิดฟีเจอร์ AI ตั้งต้น (admin เปลี่ยนแทนได้ในหน้า Settings) |
| Secret | `nida-bot-secret-key` | key สำหรับเซ็น cookie login |
| API key | `nida-bot-gemini` | ใช้ได้กับ Gemini API เท่านั้น |
| Service account | `nida-bot-run` | รันแอป อ่าน secret และใช้ bucket ข้อมูลได้เท่านั้น |
| Service account | `nida-bot-build` | build image และ push เข้า Artifact Registry |

## รับเวอร์ชันใหม่

ดึงโค้ดล่าสุดแล้วรัน

```bash
./scripts/deploy.sh --project <PROJECT_ID>
```

image จะติด tag เป็นเวลาและ commit ที่ build ถ้าต้องการย้อนกลับไปเวอร์ชันก่อน ให้ดู tag ที่ Artifact Registry แล้วรัน

```bash
./scripts/deploy.sh --project <PROJECT_ID> --image <IMAGE เดิม>
```

## เปลี่ยน model หรือขนาดเครื่อง

model เลือกได้จากหน้า Settings ของ admin site โดยไม่ต้อง deploy ใหม่ ส่วน `GEMINI_MODEL` (แชต วิเคราะห์ความรู้สึก และคำแนะนำ) และ `GEMINI_EVAL_MODEL` (ประเมินผลเมื่อปิดเคส) ใน `deploy.env` เป็นค่าที่ใช้เมื่อ admin เลือก "Deployment default"

ถ้าจะเปลี่ยนค่าตั้งต้นเหล่านี้ หรือขนาดเครื่อง เช่น `CPU`, `MEMORY` และ `MAX_INSTANCES` ให้แก้ใน `deploy.env` แล้วรัน `scripts/deploy.sh` อีกครั้ง

## เปลี่ยน API key หรือรหัสผ่าน

```bash
./scripts/rotate-secret.sh google-api-key --project <PROJECT_ID>
./scripts/rotate-secret.sh admin-password --project <PROJECT_ID>
./scripts/rotate-secret.sh user-password --project <PROJECT_ID>
./scripts/rotate-secret.sh ai-password --project <PROJECT_ID>
./scripts/rotate-secret.sh secret-key --project <PROJECT_ID>
```

Gemini API key และรหัสเปิด AI เปลี่ยนจากหน้า Settings ของ admin site ได้ง่ายกว่า คำสั่งด้านบนใช้เปลี่ยนค่าตั้งต้นใน Secret Manager ซึ่งจะมีผลเฉพาะเมื่อ admin ไม่ได้ตั้งค่าแทนไว้ `google-api-key` จะถาม key ใหม่ (ไม่แสดงบนจอ) ส่วนรหัสผ่านจะสุ่มใหม่และแสดงบนจอ `secret-key` ใช้เมื่อต้องการบังคับให้ทุกคน login ใหม่ ทุกแบบจะเพิ่ม version ใหม่ใน Secret Manager แล้วสร้าง revision ใหม่ให้ Cloud Run ใช้ค่านี้

## ดู log

```bash
gcloud run services logs read nida-bot --region asia-southeast1 --project <PROJECT_ID>
```

## ค่าใช้จ่าย

ค่าตั้งต้นเน้นประหยัด instance ละ 1 vCPU, หน่วยความจำ 512 MiB (ทดสอบแล้วใช้จริงราว 130 MiB), worker เดียวแบบ async ซึ่งรับได้หลาย request พร้อมกันเพราะเวลาส่วนใหญ่รอ Gemini ตอบ และมีได้สูงสุด 2 instance

Cloud Run คิดค่า CPU และหน่วยความจำเฉพาะช่วงที่มี request เมื่อ `MIN_INSTANCES=0` และไม่มีผู้ใช้จะไม่มีค่าใช้จ่ายส่วนนี้ แต่ request แรกหลังว่างนานจะช้ากว่าปกติหลายวินาที ถ้าต้องการให้ตอบเร็วตลอดให้ตั้ง `MIN_INSTANCES=1` ซึ่งมีค่าใช้จ่ายรายเดือนคงที่

ข้อมูลใน Cloud Storage มีขนาดเล็ก (ผลประเมินเคสละไม่กี่ KB) และแชตที่ค้างจะถูกลบเองหลัง 1 วัน ค่าเก็บข้อมูลจึงต่ำมาก ค่าใช้จ่ายหลักของระบบคือการเรียก Gemini API ซึ่งขึ้นกับจำนวนข้อความและ model ที่เลือก

## ลบระบบ

```bash
./scripts/destroy-gcp.sh --project <PROJECT_ID>
```

ต้องพิมพ์ชื่อ project ยืนยัน และสคริปต์จะถามแยกว่าจะลบ bucket ข้อมูลด้วยหรือไม่

## รันบนเครื่องสำหรับพัฒนา

คัดลอก `.env.example` เป็น `.env` ใส่ `GOOGLE_API_KEY`, `ADMIN_PASSWORD`, `USER_PASSWORD` และ `AI_PASSWORD` แล้วรัน `pip install -r requirements.txt` และ `python main.py` จากนั้นเปิด http://localhost:8000 ข้อมูลจะเก็บในโฟลเดอร์ `log/`
