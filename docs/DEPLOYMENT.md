# Deploying — Vercel in front, the server behind it

## The short version

Vercel serves the desktop UI. It **cannot** run the server, so the server goes on
a host that gives it a disk, and Vercel proxies through to it. One address for
everyone; the phone app and the APK need no change beyond typing that address.

```
                    ┌──────────────────────────────────┐
  browser ────────► │  essa.vercel.app                 │
  phone / APK ────► │  · React UI (static, built here) │
                    │  · everything else → proxied ────┼──┐
                    └──────────────────────────────────┘  │
                                                          ▼
                              ┌──────────────────────────────────────┐
                              │  the server (Render / Railway / VPS) │
                              │  · FastAPI  /api                     │
                              │  · phone app  /m                     │
                              │  · retail shop (Flask)  /pos         │
                              │  · persistent disk at /data          │
                              └──────────────────────────────────────┘
```

## Why the server is not on Vercel

Vercel runs serverless functions on a read-only filesystem, with `/tmp` wiped
between invocations. This app writes constantly and holds state in one process:

| What it writes | Where | On Vercel |
|---|---|---|
| Warehouse database | `essa.db` (SQLite) | Every GRN, LR entry and product vanishes |
| Shop database | `textile_shop.db` | Every sale vanishes |
| Invoice images | `uploads/` | Uploaded, then gone |
| Vision API key | `settings.json` | Cannot be saved |

It also writes *before serving a single request* — `app/main.py` runs
`create_all()`, `_migrate()` and `users.seed()` at import. On a read-only
filesystem that is a crash at cold start, not a degraded mode.

Two more, independent of storage: `pytesseract` shells out to a **tesseract**
binary and `pdf2image` needs **poppler**, neither of which exists on Vercel's
Python runtime; and the shop is a **Flask WSGI app mounted in-process**
(`app/pos_mount.py`) with its own login sessions, which assumes one long-running
server.

Putting the whole thing on Vercel is not a config change — it is a rewrite:
Postgres instead of SQLite, blob storage instead of `uploads/`, vision-only
extraction, and the shop rebuilt as functions. The setup below keeps your Vercel
account as the front door without any of that.

## Why Vercel proxies instead of just linking

The frontend calls `/api/...` and the shop is framed from `/pos/...` — both
relative (`App.jsx`, `PosScreen`). They have to look same-origin to the browser:

- The shop's **Flask session cookie** would be third-party inside a cross-origin
  frame and browsers drop it — the POS would ask for a login it could never
  accept. This is the same reason the shop is mounted in-process rather than run
  on its own port; see the note at the top of `app/pos_mount.py`.
- The **`essa_token` cookie** added for access control is what carries the
  requests the app cannot put a header on: `<img>` tags pointing at invoice
  scans, and label sheets opened in a new tab.

The `rewrites` in `vercel.json` make Vercel fetch those paths server-side, so the
browser only ever sees one origin. That is why the answer is a rewrite and not a
redirect — a redirect would expose the second origin and break both cookies.

---

## 1 · The server

### Build it

The `Dockerfile` at the repo root has the two OCR binaries and both codebases.
Any host that runs a container works — the examples below are Render (simplest
disk story) and Railway.

### Render

1. **New → Web Service**, point at this repo, Runtime **Docker**.
2. **Disks → Add Disk**: mount path `/data`, 1 GB is plenty to start.
   Without this, every deploy starts an empty warehouse.
3. Set the environment variables in the table below.
4. Deploy, then note the URL — `essa-intake.onrender.com` or similar.

> On Render's free tier a service sleeps after inactivity and takes ~30s to wake.
> The warehouse opening at 9am would wait for that first load. A paid instance
> stays warm.

### Railway

Same, with **Volumes → New Volume** mounted at `/data`. Railway injects `PORT`;
the Dockerfile's `CMD` already reads it.

### Environment variables

| Variable | Set it to | Why |
|---|---|---|
| `ESSA_AUTH_SECRET` | a long random string | **Set this.** It signs every login token. The default is a literal placeholder in the repo, and anyone who has read the source could mint a super admin token for your server. Generate: `python -c "import secrets;print(secrets.token_hex(32))"` |
| `ESSA_SUPERADMIN_PASSWORD` | your own | Defaults to `super@123`, which is in this repo |
| `ESSA_ADMIN_PASSWORD` | your own | Defaults to `essa@123` |
| `ESSA_USER_PASSWORD` | your own | Defaults to `user@123` |
| `ESSA_SUPERBOSS_USER` / `ESSA_SUPERBOSS_PASSWORD` | your own, or leave unset | Optional. The Super Boss — the rank above super admin — has **no default password on purpose**, so no deployment gets a well-known login to the account that can manage everyone else. Leave both unset and a super admin appoints the first Super Boss from Users & Access (the role is offered there only while nobody holds it). Set both to create one at startup instead |
| `ESSA_UTC_OFFSET_MINUTES` | `330` (default, India) | What "today" means on the Command Center. Every timestamp is stored in UTC; a shop that opens at 9am would otherwise file its first hours under yesterday |
| `ESSA_STATE_DIR` | `/data` | Already set by the Dockerfile |
| `DATABASE_URL` | `sqlite:////data/textile_shop.db` | Already set by the Dockerfile. Named generically because the shop reads it — if the host attaches a managed Postgres it may overwrite this, pointing the shop at a database with none of its tables |
| `ANTHROPIC_API_KEY` | your key | Optional — can also be typed into the settings screen instead |
| `ESSA_COMPANY_NAME` / `ESSA_COMPANY_GSTIN` | yours | Appears on the invoices |

**There is no `.env` file.** This backend reads real environment variables only
— nothing calls `load_dotenv`, so a `.env` dropped beside the code is read by
nobody and the defaults stay in force. Set them in the host's dashboard.

### Set the passwords BEFORE the first deploy

The three accounts are seeded on first boot and **only if missing**. That is
what stops a redeploy reverting a password someone changed in the app — but it
cuts both ways:

```
deploy → boot with defaults → set ESSA_ADMIN_PASSWORD → redeploy
                                                    ↳ no effect, still essa@123
```

Once the row exists the environment variable is never consulted again. If the
server has already booted once, the password has to be changed from the **Users
& Access** screen (or `POST /api/users/{id}/password`) instead. Setting these
before the very first boot is the only way the variables do anything.

`ESSA_AUTH_SECRET` is different — it is read on every request, so it takes
effect the moment the server restarts. The cost is that every token signed with
the old secret stops verifying, which signs everyone out of both apps. That is
an acceptable one-off, and a good reason to set it before people start using
the deployment rather than after.

### Check it before wiring Vercel up

```bash
curl https://YOUR-SERVER.onrender.com/api/status
curl -X POST https://YOUR-SERVER.onrender.com/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"username":"superadmin","password":"YOUR-PASSWORD"}'
```

The first should report the company and providers; the second should return a
token and `"role":"superadmin"`.

## 2 · Vercel

1. **Add New → Project**, import this repo.
2. Leave the build settings alone — `vercel.json` sets them.
3. **Edit `vercel.json` first**: replace every `REPLACE-ME.onrender.com` with the
   server's hostname. There are six. Vercel does not expand environment
   variables inside `rewrites`, so the host really does have to be written in.
4. Deploy.

Then open the Vercel URL and sign in. If the UI loads but every action fails,
the rewrites are pointing at the wrong host — that is the one thing to check
first.

## 3 · The phone

**The web app** is at `<your-vercel-url>/m`, proxied through to the server. It
can be added to a home screen as a PWA.

**The APK** needs no rebuild for a new address. On first run it asks for the
server; type the Vercel hostname:

```
essa.vercel.app
```

`SetupActivity.normalise` reads a domain as https on port 443, and a bare IP or
machine name as the LAN server on port 8000 — so the same APK serves both the
office PC and the deployment. It checks `/api/status` before saving, so a wrong
address is reported on that screen rather than failing later.

**A real gain from HTTPS:** the in-app camera and `BarcodeDetector` are only
exposed by Chromium on a secure origin, which `http://192.168.1.5:8000` is not —
the reason scanning had to be done natively (`android/README.md`). On an HTTPS
deployment the web scanner works too.

---

## Known limits of proxying through Vercel

Two are worth knowing before the first busy day. **Both are untested against
your actual traffic — verify them with a real invoice before relying on this in
the warehouse**, because the exact ceilings depend on your Vercel plan and are
not something this repo can assert:

1. **Upload size.** Invoice photographs in `backend/data/uploads/` run from
   150 KB to 1.3 MB; Vercel documents a 4.5 MB body limit for its own functions.
   A large multi-page PDF is the case to try.
2. **Slow requests.** A dense invoice through vision extraction can take a
   minute. Proxied requests are subject to Vercel's response timeout.

If either bites, the fix does not require moving off Vercel: give the server its
own subdomain of a domain you control — `app.example.com` for Vercel,
`api.example.com` for the server — and point the frontend's uploads straight at
the server. Because both are under one registrable domain the browser still
treats them as same-site, so the POS session and the `essa_token` cookie keep
working. That is the better long-term setup anyway, and it is the only one that
needs a custom domain rather than a `.vercel.app` one.

## Backups

The whole warehouse is two SQLite files and a folder of images, all under
`/data`. On Render, **Disks → Snapshots**. Worth having before you need it —
`/api/documents/clear-all` empties every transaction table in one call, which is
why it is restricted to a super admin.
