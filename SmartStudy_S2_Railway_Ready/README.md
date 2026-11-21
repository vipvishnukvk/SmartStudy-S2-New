# Smart Study S² Global

**A Path to Your Board**

A **working, deployable MVP** of a multi-school learning and exam-preparation web app. No paid AI API required for its core features. The previous fixed Sai/Suma local account arrangement has been replaced by individual email accounts and permission-based classrooms. It runs locally right away; **it is not automatically online merely by extracting this ZIP**.

## What works in this version

| Area | Function |
|---|---|
| Student | Email account, join any teacher's class with unique code, request approval, study across schools |
| Teacher | Email account, administrator verification, create independent subject classes, approve students, assign worksheets and MCQ tests |
| Teacher content | Upload actual worksheet images/PDFs, create MCQs manually, set deadlines, view submissions |
| Private discussion | Per-assignment, per-student comments visible only to that student and his/her own subject teacher |
| Assessment | Automatic marking of teacher-authored objective MCQs, handwritten photo/PDF submission, manual scoring and red digital annotations on images |
| Parent | Request a link to multiple children; the student approves; separate read-only progress and attendance views |
| Admin | Create securely via setup, verify teachers, view users/classes, suspend/restore classes, upload/verify globally shared papers |
| Shared library | Admin-uploaded, verified PDFs and official source URLs searchable by subject, year, set, grade and board |
| Planner | Personal date navigation with dedicated prev/next arrows and separate date picker; optional original CBSE Class 10 Board 2027 template |
| Reporting | 6-subject Class 10 overview plus configured subjects, assignment completion, MCQ results, teacher-recorded attendance |

**Not yet included**: scanning arbitrary PDFs to automatically generate questions, generative AI, automated handwritten long-answer assessment, live push notifications, student email verification, comprehensive content moderation, student/parent age and consent handling, or authentic 2022–2026 previous-year paper PDFs in the download. Do not describe these as working. Existing CBSE planner timetable and chapter list are included as an **optional personal template**, not forced on all users.

The global paper library begins **empty**, to avoid inventing years/sets or redistributing copyrighted papers without appropriate permission. An admin can add source-verified papers as legally permitted.

## Local Windows start

1. Install Python **3.10+** and make sure Python is available in PATH.
2. Extract this ZIP and double-click `START_WINDOWS.bat`. It installs Python dependencies once, and launches the server.
3. If the browser does not open automatically, go to `http://127.0.0.1:8000`.
4. To create your first administrator, open a second terminal in the extracted folder and run `python setup_admin.py`, then choose a private admin email/password. Restart not required. No universal preset admin password is distributed.
5. Create student / parent accounts using the site. Teacher accounts must be verified by an administrator under **Users & Classes** before they can create classes.

Mac/Linux: `sh START_MAC_LINUX.sh`.

The default database is `smartstudy.sqlite3` and local uploaded files are in `uploads/`. **Back up both**. This is a local pilot until you deploy it on an online server.

## Public online deployment

A hosting account is required. The application code is already packaged for Docker or Python ASGI hosting, but I could not provision a live public endpoint from the present connection. The Replit integration reported **`requires_active_subscription`**. This limitation is from hosting authorization, **not** the lack of an AI API.

Choose a host that supports Python ASGI/Docker, HTTPS, persistent storage and a database (for example, Render, Railway, a properly configured VPS, or an active Replit deployment). The Dockerfile runs Uvicorn on the host-provided `PORT` and binds to `0.0.0.0`. Configure:

- `DATABASE_URL`: preferably managed PostgreSQL URL using `postgresql+psycopg://...`. SQLAlchemy supports PostgreSQL when the supplied driver is installed. SQLite is acceptable for a small single-server trial only.
- `FILES_DIR`: **persistent** writable upload directory shared consistently with the app. Ephemeral hosts lose uploads during redeploy. For multiple server replicas use an authenticated object-storage backend, which is not implemented in this MVP.
- `COOKIE_SECURE=1` on public HTTPS domains.
- Create admin securely by setting `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` (12+ characters), then redeploy; or run `setup_admin.py` in a secure hosting console. Don't use the known previous `Admin@2027` password on a public site.
- Optional Google OAuth configuration as below.

A sample container command for an operator is `docker build -t smartstudy-s2 .` followed by container startup with the required environment variables and persistent volumes. Your host may have its own import/build/deploy workflow.

**Note**: Hosting and storage often cost money once you have many real students or files. The platform core **does not use a paid AI API**, but no developer can promise unlimited worldwide hosting for free.

## Google sign-in setup (OAuth, not a paid AI API)

1. In Google Cloud Console, create an OAuth consent screen (public/External or organizational/Internal as appropriate), set the app's authorized domain and relevant privacy/policy details.
2. Create a **Web application** OAuth client.
3. Set **Authorized redirect URI** to `https://YOUR-DOMAIN/auth/google/callback`.
4. Set these hosting secret environment variables: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.
5. Redeploy. The **Continue with Google** option is shown automatically only when both OAuth credentials are configured. OAuth callback/session state and Google ID-token signature are verified by the server.
6. Test both first-time Google login and linking an existing verified-email account. Some school-managed Google accounts may prohibit external apps until an administrator approves them.

This requires Google's standard authentication service (an API technically), **not a paid generative AI API**. Without OAuth configuration, the email/password registration works.

## How to use it

1. **Admin** registers teachers by verifying identities in the admin panel. Admin can upload school-neutral past papers, verify their legitimacy, and choose when all students/teachers can see them.
2. **Teacher** creates a class, for example Maths, DPS, Class 10. A unique join code is displayed.
3. **Student** creates an account and enters that join code. Teacher approves. Student can join English in a different school with another code.
4. **Teacher** adds a worksheet with a PDF/image or creates an MCQ quiz/mock test and a deadline.
5. **Student** opens the assignment and uploads a photo/PDF or completes MCQs. Only that student's private comments are visible to that student and the class teacher.
6. **Teacher** views uploaded answer images, draws red-pen strokes, enters marks and feedback, and returns the paper. Images support canvas annotation; PDFs currently support original viewing plus written feedback but not canvas annotation.
7. **Parent** enters child's registered email, student explicitly approves the link. Parent sees multiple children independently, with no ability to change marks.
8. **Student** may import the **CBSE Class 10 2027** template into *their own* planner. Buttons `‹` and `›` move one day; the separate date input jumps to a selected day.

## Data security and child safety

This is a **pilot-ready functional prototype, not yet a compliance-reviewed worldwide production service for children**. Before opening it to minors at scale, implement independent security review, verified guardian consent and age handling as required locally, privacy notices and data-retention policies, abuse reporting/moderation, email verification/recovery, content-scanning, backup/restore drills, access logs, monitoring, application security tests, and a privacy review covering each country where you operate. Protect uploaded papers and private discussions, respect third-party copyright, and establish school policies for teacher validation.

Already present: hashed passwords (PBKDF2), database-backed sessions, HttpOnly cookies, optional Secure cookies, CSRF checks, role-based server authorization, student approval for parent links, teacher verification and teacher-scoped reporting, upload limits and file type checks, restrictive download authorization, basic login rate limits, and no public self-registration as admin.

## Developer checks

Run `python selftest.py` **only with a disposable database** using `DATABASE_URL=sqlite:///./test.sqlite3` and optional test admin settings, not against real records. It creates test accounts, teachers, classes, student submissions and a sample paper.

The app does not promise real-time WebSocket push. Records are saved on each action, so teachers/parents see current data when visiting or refreshing their dashboards.

Study-plan data is sourced from the 28-page CBSE Class 10 Board Exam 2027 planner previously uploaded in this conversation. Pre-board 2026–27 and exam dates are the source document's values, with local Qatar scheduling where specified. As a global platform, users must confirm their own board/school dates and time zone.

© Independent Smart Study S² prototype. Not affiliated with Google or CBSE.
