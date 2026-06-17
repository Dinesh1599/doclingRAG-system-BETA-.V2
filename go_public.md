# Go Public — share the demo with a remote manager

Run each block in its **own terminal**. Keep this Mac **awake** while sharing.
Tunnel URLs/addresses are **new every run** — copy the one each script prints.

---

## 0. One-time prerequisites (mostly already done)
- **Docker running**, Postgres up:  `docker start rag-postgres`
- **`.env`** has `OPENAI_API_KEY`, `DATABASE_URL`, `CHAT_USERNAME`, `CHAT_PASSWORD`
- **DB hardened:** strong `rag` password + read-only `manager_ro` user ✅
- **Installed:** `cloudflared`, `ngrok`
- **ngrok (once):**  `ngrok config add-authtoken <YOUR_TOKEN>`  (free signup at ngrok.com)

---

## 1. Chat app  (ask questions + upload PDFs)
```bash
./scripts/share_chat.sh
```
- **Share:** the printed `https://….trycloudflare.com` URL
- **Login:** `CHAT_USERNAME` / `CHAT_PASSWORD` (from your `.env`)

---

## 2. Postgres  (read-only SQL access)
```bash
./scripts/share_db.sh
```
- It prints `tcp://HOST:PORT`. In **DBeaver** (New → PostgreSQL, Connect by **Host**):
  - Host = `HOST`  ·  Port = `PORT`  ·  Database = `rag`
  - User = `manager_ro`  ·  Password = *(the manager_ro password you saved)*
  - **SSL mode = disable**
- ⚠️ SSMS won't work — it's PostgreSQL. Use DBeaver / pgAdmin / Azure Data Studio.

---

## 3. Airflow UI
```bash
./scripts/run_airflow.sh        # terminal A — starts Airflow on :8080
./scripts/share_airflow.sh      # terminal B — public URL
```
- **Share:** the printed `https://….trycloudflare.com` URL
- **Login:** `admin` / *(password in `.airflow/simple_auth_manager_passwords.json.generated`)*

---

## Stop sharing
`Ctrl+C` in each terminal. (Postgres + Docker keep running locally; that's fine.)

## Notes
- Only the **chat** and **Airflow** are HTTPS (port 443) — most reliable through
  corporate firewalls. **Postgres over ngrok-TCP** can be blocked by a manager's
  office network; if it times out, have them use the chat instead.
- The manager only needs the **chat** to query the data — Postgres access is
  optional (for raw SQL/BI).
