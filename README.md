# Hermiss 鍗曠敤鎴风増

Hermiss 鏄熀浜?Hermes agent 寮€鍙戠殑鑷儴缃茶櫄鎷熸亱浜洪櫔浼村姪鎵嬨€傚畠鍙互閫氳繃寰俊涓庝綘鑷劧鑱婂ぉ锛屾敮鎸侀暱鏈熻蹇嗐€佷汉璁剧鐞嗐€佽〃鎯呭寘绯荤粺鍜屼富鍔ㄥ洖澶嶃€?
杩欎釜浠撳簱鏄?**鍗曠敤鎴烽儴缃茬増**锛氭病鏈夊鐢ㄦ埛绠＄悊鍛樼锛岀櫥褰曞悗绠＄悊鐨勫氨鏄綘鑷繁鐨?Hermiss 瀹瑰櫒銆?
## 鍔熻兘鐗圭偣

- **寰俊鎵爜缁戝畾**锛氶€氳繃闈㈡澘鎵爜缁戝畾寰俊锛屾棤闇€鎵嬪姩濉啓澶嶆潅閰嶇疆銆?- **妯″瀷閰嶇疆**锛氶粯璁?DeepSeek锛屽彲鍦ㄩ潰鏉块噷閰嶇疆 provider銆乵odel銆乥ase_url 鍜?API Key銆?- **闀挎湡璁板繂**锛氭敮鎸佺敤鎴峰亸濂姐€佺姸鎬佸拰鏈€杩戜簨浠剁殑璁板繂涓庢绱€?- **浜鸿绠＄悊**锛氭敮鎸佺紪杈?`SOUL.md`銆乣USER.md`锛屼篃鏀寔 AI 鐢熸垚浜鸿鑽夌銆?- **琛ㄦ儏鍖呯郴缁?*锛氭敮鎸佸垎绫汇€佷笂浼犮€侀瑙堛€佹敼鍚嶃€佺Щ鍔ㄥ拰璋冪敤璁板綍銆?- **涓诲姩鍥炲**锛氬璇濈粨鏉熷悗鍙牴鎹渶杩戜笂涓嬫枃鐢熸垚鍥炶銆?- **涓€閿儴缃?*锛氱敤鎴峰彧闇€瑕佸畨瑁?Docker锛岃剼鏈細鑷姩鎷夊彇闀滃儚骞跺惎鍔ㄩ潰鏉裤€?
## 浠撳簱缁撴瀯

```text
.
鈹溾攢鈹€ panel/                    # 鍗曠敤鎴烽潰鏉挎簮鐮?鈹溾攢鈹€ docs/                     # 閮ㄧ讲鎵嬪唽
鈹溾攢鈹€ docker-compose.yml        # 闈㈡澘缂栨帓鏂囦欢
鈹溾攢鈹€ .env.example              # 鐜鍙橀噺绀轰緥
鈹溾攢鈹€ 涓€閿儴缃?bat              # Windows 涓€閿儴缃插叆鍙?鈹溾攢鈹€ 涓€閿儴缃?ps1              # Windows PowerShell 閮ㄧ讲鑴氭湰
鈹溾攢鈹€ 浣跨敤璇存槑.txt              # 绠€鐭娇鐢ㄨ鏄?鈹斺攢鈹€ README.md
```

> 娉ㄦ剰锛氫粨搴撲笉鍐嶆惡甯?`hermiss.tar.gz` 鍜?`milvus.tar.gz`銆侶ermiss 涓荤▼搴忛暅鍍忔斁鍦?GitHub Packages锛孧ilvus 浣跨敤瀹樻柟 Docker 闀滃儚銆?
## 闀滃儚鏉ユ簮

| 缁勪欢 | 闀滃儚 |
| --- | --- |
| Hermiss 涓荤▼搴?| `ghcr.io/linmumupro/hermiss:single` |
| Milvus 鍚戦噺鏁版嵁搴?| `milvusdb/milvus:v2.4.0` |
| Hermiss 闈㈡澘 | 鏈粨搴?`panel/` 鏈湴鏋勫缓 |

## 蹇€熷紑濮嬶細Windows

1. 瀹夎骞跺惎鍔?Docker Desktop銆?2. 涓嬭浇鎴?clone 鏈粨搴撱€?3. 鍙屽嚮 `涓€閿儴缃?bat`銆?4. 鎵撳紑闈㈡澘锛?
```text
http://127.0.0.1:8788
```

榛樿璐﹀彿瀵嗙爜锛?
```text
璐﹀彿锛歨ermiss
瀵嗙爜锛歨ermiss
```

## 蹇€熷紑濮嬶細Linux / macOS / WSL

```bash
git clone https://github.com/LinMuMuPro/hermiss.git
cd hermiss
cp .env.example .env
docker compose up -d --build
```

璁块棶锛?
```text
http://127.0.0.1:8788
```

## 閰嶇疆椤?
澶嶅埗 `.env.example` 涓?`.env` 鍚庡彲浠ヤ慨鏀癸細

```env
PANEL_HOST=127.0.0.1
PANEL_PORT=8788
PANEL_USERNAME=hermiss
PANEL_PASSWORD=hermiss
SECRET_KEY=change-me-hermiss-single-user
HERMISS_CONTAINER=hermiss-single
HERMISS_CONTAINER_PORT=8770
DOCKER_IMAGE=ghcr.io/linmumupro/hermiss:single
```

濡傛灉浣犳兂璁╁眬鍩熺綉鎵嬫満璁块棶闈㈡澘锛屽彲浠ユ妸锛?
```env
PANEL_HOST=0.0.0.0
```

鐒跺悗璁块棶鐢佃剳灞€鍩熺綉 IP锛屼緥濡傦細

```text
http://192.168.x.x:8788
```

## 甯哥敤鍛戒护

```bash
# 鍚姩
docker compose up -d --build

# 鏌ョ湅鐘舵€?docker compose ps

# 鏌ョ湅鏃ュ織
docker compose logs -f

# 鍋滄
docker compose down

# 鏇存柊 Hermiss 涓荤▼搴忛暅鍍?docker pull ghcr.io/linmumupro/hermiss:single
docker compose up -d --build
```

## 棣栨浣跨敤娴佺▼

1. 鐧诲綍闈㈡澘銆?2. 鍦ㄨ缃〉閰嶇疆妯″瀷 API Key銆?3. 鎵爜缁戝畾寰俊銆?4. 鍦ㄤ汉璁鹃〉纭鎴栦慨鏀逛汉璁俱€?5. 鍦ㄨ〃鎯呭寘椤典笂浼犺嚜宸辩殑琛ㄦ儏鍖呫€?6. 寮€濮嬪拰 Hermiss 鑱婂ぉ銆?
## 璇︾粏鏂囨。

瀹屾暣閮ㄧ讲璇存槑瑙侊細

```text
docs/Hermiss鍗曠敤鎴烽儴缃叉墜鍐?md
```

## 璇存槑

- Hermiss 鏄櫔浼村瀷铏氭嫙鎭嬩汉鍔╂墜锛屼笉鏄鏈嶆満鍣ㄤ汉銆?- 璇疯嚜琛屼繚绠?API Key銆佸井淇¤处鍙峰拰鏈湴鏁版嵁銆?- 濡傛灉鎷夊彇 `ghcr.io/linmumupro/hermiss:single` 鎻愮ず鏃犳潈闄愶紝璇风‘璁?GitHub Packages 涓闀滃儚宸茶缃负 Public銆?
