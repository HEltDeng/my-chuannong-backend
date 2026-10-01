"""Independent local preview, credentials never enter command arguments or logs."""
from pathlib import Path
import json, os
root=Path(__file__).resolve().parent
credentials=Path(os.environ.get("MY_CHUANNONG_LOCAL_CREDENTIALS",r"D:\Codex-Local-Secrets\MyChuannong\backend-local.json"))
c=json.loads(credentials.read_text())
values={"ENV":"development","HOST":"127.0.0.1","PORT":"8792","DB_PATH":str(root/"data/my-chuannong-v105.db"),"ADMIN_USERNAME":c["adminUsername"],"ADMIN_PASSWORD":c["adminPassword"],"PHONE_HASH_SECRET":c["phoneHashSecret"],"DEVICE_KEY_SECRET":c["deviceKeySecret"],"PII_ENCRYPTION_KEY":c["piiEncryptionKey"]}
for key,value in values.items():os.environ["MY_CHUANNONG_"+key]=value
import server
server.main()
