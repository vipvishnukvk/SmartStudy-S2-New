"""Smart Study S²: API-key-free global classroom and exam preparation MVP.

Run: uvicorn app:app --host 0.0.0.0 --port 8000
Deploy with DATABASE_URL pointing to PostgreSQL and persistent FILES_DIR storage.
"""
import base64, hashlib, hmac, json, os, re, secrets, shutil, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker
import httpx
import jwt

BASE=Path(__file__).resolve().parent
DB_URL=os.getenv('DATABASE_URL',f'sqlite:///{BASE / "smartstudy.sqlite3"}')
if DB_URL.startswith('postgres://'): DB_URL='postgresql+psycopg://'+DB_URL[len('postgres://'):]
if DB_URL.startswith('postgresql://'): DB_URL='postgresql+psycopg://'+DB_URL[len('postgresql://'):]
engine=create_engine(DB_URL,connect_args={'check_same_thread':False} if DB_URL.startswith('sqlite:') else {}, pool_pre_ping=True)
Session=sessionmaker(bind=engine,expire_on_commit=False)
BaseModelSQL=declarative_base()
FILES=Path(os.getenv('FILES_DIR',str(BASE/'uploads'))); FILES.mkdir(parents=True,exist_ok=True)
COOKIE_SECURE=os.getenv('COOKIE_SECURE','0')=='1'; SESSION_TTL=86400*14
MAX_UPLOAD=12*1024*1024
app=FastAPI(title='Smart Study S²',version='1.0.0')


def utcnow(): return datetime.now(timezone.utc).replace(tzinfo=None)
def iso(d): return d.isoformat()+'Z' if d else None
class User(BaseModelSQL):
    __tablename__='users'
    id=Column(Integer,primary_key=True); email=Column(String(320),unique=True,nullable=False,index=True)
    name=Column(String(120),nullable=False); role=Column(String(20),nullable=False)
    password_hash=Column(Text); google_sub=Column(String(160),unique=True,nullable=True)
    school=Column(String(200),default=''); grade=Column(String(50),default='Class 10')
    created=Column(DateTime,default=utcnow); teacher_verified=Column(Boolean,default=False)
class LoginSession(BaseModelSQL):
    __tablename__='sessions'
    id=Column(Integer,primary_key=True); user_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    token_hash=Column(String(64),unique=True,nullable=False,index=True)
    csrf=Column(String(60),nullable=False); expiry=Column(DateTime,nullable=False)
class LoginFailure(BaseModelSQL):
    __tablename__='login_failures'
    id=Column(Integer,primary_key=True); email=Column(String(320),index=True)
    ip_hash=Column(String(64),index=True); created=Column(DateTime,default=utcnow)

class OAuthState(BaseModelSQL):
    __tablename__='oauth_states'
    state_hash=Column(String(64),primary_key=True); role=Column(String(20)); expiry=Column(DateTime)
class Classroom(BaseModelSQL):
    __tablename__='classes'
    id=Column(Integer,primary_key=True); teacher_id=Column(Integer,ForeignKey('users.id'),nullable=False)
    subject=Column(String(70),nullable=False); school=Column(String(200),default='')
    grade=Column(String(70),default='Class 10'); name=Column(String(160),nullable=False)
    code=Column(String(12),unique=True,nullable=False,index=True); created=Column(DateTime,default=utcnow); suspended=Column(Boolean,default=False)
class Enrollment(BaseModelSQL):
    __tablename__='enrollments'; __table_args__=(UniqueConstraint('class_id','student_id'),)
    id=Column(Integer,primary_key=True); class_id=Column(Integer,ForeignKey('classes.id'),nullable=False)
    student_id=Column(Integer,ForeignKey('users.id'),nullable=False); status=Column(String(15),default='pending'); created=Column(DateTime,default=utcnow)
class Attendance(BaseModelSQL):
    __tablename__='attendance'; __table_args__=(UniqueConstraint('class_id','student_id','day'),)
    id=Column(Integer,primary_key=True); class_id=Column(Integer,ForeignKey('classes.id'))
    student_id=Column(Integer,ForeignKey('users.id')); day=Column(String(10)); status=Column(String(15)); recorded=Column(DateTime,default=utcnow)
class Assignment(BaseModelSQL):
    __tablename__='assignments'
    id=Column(Integer,primary_key=True); class_id=Column(Integer,ForeignKey('classes.id'),nullable=False)
    title=Column(String(200),nullable=False); instructions=Column(Text,default=''); due_date=Column(String(10),default='')
    kind=Column(String(18),default='worksheet'); max_score=Column(Float,default=100)
    questions=Column(Text,default='[]'); created=Column(DateTime,default=utcnow)
    original_filename=Column(String(255),default=''); storage_key=Column(String(100),default=''); mime=Column(String(100),default='')
class Submission(BaseModelSQL):
    __tablename__='submissions'; __table_args__=(UniqueConstraint('assignment_id','student_id'),)
    id=Column(Integer,primary_key=True); assignment_id=Column(Integer,ForeignKey('assignments.id'))
    student_id=Column(Integer,ForeignKey('users.id')); notes=Column(Text,default='')
    original_filename=Column(String(255),default=''); storage_key=Column(String(100),default='')
    mime=Column(String(100),default=''); answers=Column(Text,default='{}')
    score=Column(Float); feedback=Column(Text,default=''); strokes=Column(Text,default='[]')
    status=Column(String(15),default='submitted'); submitted=Column(DateTime,default=utcnow)
class Comment(BaseModelSQL):
    __tablename__='comments'
    id=Column(Integer,primary_key=True); assignment_id=Column(Integer,ForeignKey('assignments.id'))
    student_id=Column(Integer,ForeignKey('users.id')); author_id=Column(Integer,ForeignKey('users.id'))
    body=Column(Text,nullable=False); created=Column(DateTime,default=utcnow)
class ParentLink(BaseModelSQL):
    __tablename__='parent_links'; __table_args__=(UniqueConstraint('parent_id','student_id'),)
    id=Column(Integer,primary_key=True); parent_id=Column(Integer,ForeignKey('users.id'))
    student_id=Column(Integer,ForeignKey('users.id')); status=Column(String(15),default='pending'); created=Column(DateTime,default=utcnow)
class PlannerTask(BaseModelSQL):
    __tablename__='planner_tasks'
    id=Column(Integer,primary_key=True); student_id=Column(Integer,ForeignKey('users.id'))
    day=Column(String(10),nullable=False); subject=Column(String(75),default='General')
    timing=Column(String(80),default=''); title=Column(Text,nullable=False)
    completed=Column(Boolean,default=False); source=Column(String(18),default='custom')
class ChapterCheck(BaseModelSQL):
    __tablename__='chapters'; __table_args__=(UniqueConstraint('student_id','subject','title'),)
    id=Column(Integer,primary_key=True); student_id=Column(Integer,ForeignKey('users.id'))
    subject=Column(String(75)); title=Column(String(250)); studied=Column(Boolean,default=False); revised=Column(Boolean,default=False)
class Paper(BaseModelSQL):
    __tablename__='papers'
    id=Column(Integer,primary_key=True); uploaded_by=Column(Integer,ForeignKey('users.id'))
    board=Column(String(100),default='CBSE'); subject=Column(String(90)); grade=Column(String(70),default='Class 10')
    year=Column(Integer); set_no=Column(String(30),default=''); title=Column(String(200))
    source_url=Column(String(900),default=''); original_filename=Column(String(255),default='')
    storage_key=Column(String(100),default=''); mime=Column(String(100),default='')
    verified=Column(Boolean,default=False); created=Column(DateTime,default=utcnow)

BaseModelSQL.metadata.create_all(engine)


def pass_hash(password):
    salt=os.urandom(16); v=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,260000)
    return 'pbkdf2$260000$'+salt.hex()+'$'+v.hex()
def pass_check(password,stored):
    try:
        typ,n,s,h=stored.split('$'); return typ=='pbkdf2' and hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(s),int(n)),bytes.fromhex(h))
    except (ValueError,TypeError): return False

def normemail(s): return s.strip().lower()
def require_role(u,*roles):
    if u.role not in roles: raise HTTPException(403,'Not permitted for this account')
def get_db(): return Session()
def new_session(db,u):
    raw=secrets.token_urlsafe(40); csrf=secrets.token_urlsafe(32)
    db.add(LoginSession(user_id=u.id,token_hash=hashlib.sha256(raw.encode()).hexdigest(),csrf=csrf,expiry=utcnow()+timedelta(seconds=SESSION_TTL)))
    db.commit(); return raw,csrf

def set_session_cookie(response,raw):
    response.set_cookie('ss_session',raw,httponly=True,secure=COOKIE_SECURE,samesite='lax',max_age=SESSION_TTL,path='/')

def auth(request,db,mutate=False):
    raw=request.cookies.get('ss_session','')
    if not raw: raise HTTPException(401,'Sign in required')
    s=db.query(LoginSession).filter(LoginSession.token_hash==hashlib.sha256(raw.encode()).hexdigest(),LoginSession.expiry>utcnow()).first()
    if not s: raise HTTPException(401,'Session expired')
    if mutate and not hmac.compare_digest(request.headers.get('x-csrf',''),s.csrf): raise HTTPException(403,'Session verification failed. Refresh and try again.')
    u=db.get(User,s.user_id)
    if not u: raise HTTPException(401,'Account unavailable')
    return u,s

def basic_user(u): return {'id':u.id,'name':u.name,'email':u.email,'role':u.role,'school':u.school,'grade':u.grade,'teacher_verified':bool(u.teacher_verified)}
def class_info(c,db,u=None):
    teacher=db.get(User,c.teacher_id)
    info={'id':c.id,'name':c.name,'subject':c.subject,'school':c.school,'grade':c.grade,
          'teacher':teacher.name if teacher else 'Teacher','teacher_id':c.teacher_id,
          'enrolled':db.query(Enrollment).filter_by(class_id=c.id,status='approved').count(),'suspended':bool(c.suspended)}
    if u and u.id==c.teacher_id: info['code']=c.code
    if u and u.role=='student':
        e=db.query(Enrollment).filter_by(class_id=c.id,student_id=u.id).first()
        info['my_status']=e.status if e else None
    return info

def student_access(db,u,student_id):
    if u.role=='student' and u.id==student_id: return True
    if u.role=='parent' and db.query(ParentLink).filter_by(parent_id=u.id,student_id=student_id,status='approved').first(): return True
    if u.role=='teacher' and db.query(Enrollment).join(Classroom,Classroom.id==Enrollment.class_id).filter(Enrollment.student_id==student_id,Enrollment.status=='approved',Classroom.teacher_id==u.id).first(): return True
    return False

def get_class_access(db,u,class_id):
    c=db.get(Classroom,class_id)
    if not c: raise HTTPException(404,'Class not found')
    if c.suspended:raise HTTPException(403,'Class temporarily suspended by the administrator')
    if u.role=='teacher' and c.teacher_id==u.id: return c
    if u.role=='student' and db.query(Enrollment).filter_by(class_id=class_id,student_id=u.id,status='approved').first():return c
    raise HTTPException(403,'You are not in this class')

def assignment_access(db,u,aid):
    a=db.get(Assignment,aid)
    if not a: raise HTTPException(404,'Assignment not found')
    c=get_class_access(db,u,a.class_id)
    return a,c

def file_perm(db,u,entity):
    if isinstance(entity,Paper):
        if entity.verified or u.role=='admin':return True
        return False
    a=db.get(Assignment,entity.assignment_id)
    if not a:return False
    c=db.get(Classroom,a.class_id)
    return (u.role=='teacher' and c.teacher_id==u.id) or (u.role=='student' and entity.student_id==u.id)


def write_upload(file:UploadFile):
    allowed={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','application/pdf':'.pdf'}
    ext=Path(file.filename or '').suffix.lower()
    if file.content_type not in allowed or ext not in ('.jpg','.jpeg','.png','.webp','.pdf'):
        raise HTTPException(400,'Only JPG, PNG, WEBP and PDF allowed')
    data=file.file.read(MAX_UPLOAD+1)
    if len(data)>MAX_UPLOAD: raise HTTPException(413,'File exceeds 12 MB limit')
    if not data or not (data.startswith(b'%PDF-') or data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'RIFF')):
        raise HTTPException(400,'Invalid file format')
    key=secrets.token_hex(24)+allowed[file.content_type]
    (FILES/key).write_bytes(data)
    return key

@app.middleware('http')
async def security_headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    return response

@app.get('/')
def root(): return FileResponse(BASE/'web/index.html')
app.mount('/assets',StaticFiles(directory=BASE/'web'),name='assets')
@app.get('/api/status')
def status():return {'ok':True,'google_login_enabled':bool(os.getenv('GOOGLE_CLIENT_ID') and os.getenv('GOOGLE_CLIENT_SECRET')),'ai_api_required':False}

class AuthInput(BaseModel):
    email:str;password:str;name:str='';role:str='student';school:str='';grade:str='Class 10'
@app.post('/api/register')
def register(data:AuthInput,response:Response):
    email=normemail(data.email)
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):raise HTTPException(400,'Enter a valid email')
    if data.role not in ('student','teacher','parent'):raise HTTPException(403,'Admin accounts cannot be self-registered')
    if len(data.password)<10:raise HTTPException(400,'Password must be at least 10 characters')
    if not data.name.strip():raise HTTPException(400,'Enter your name')
    with Session() as db:
        if db.query(User).filter_by(email=email).first():raise HTTPException(409,'Email already registered')
        u=User(email=email,name=data.name.strip()[:120],role=data.role,password_hash=pass_hash(data.password),school=data.school[:200],grade=data.grade[:50]);db.add(u);db.commit()
        raw,csrf=new_session(db,u);set_session_cookie(response,raw)
        return {'user':basic_user(u),'csrf':csrf}
@app.post('/api/login')
def login(data:AuthInput,response:Response,request:Request):
    with Session() as db:
        email=normemail(data.email)
        ip=hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest()
        recent=utcnow()-timedelta(minutes=10)
        tries=db.query(LoginFailure).filter(LoginFailure.created>recent,LoginFailure.email==email).count()
        by_ip=db.query(LoginFailure).filter(LoginFailure.created>recent,LoginFailure.ip_hash==ip).count()
        if tries>=8 or by_ip>=30:raise HTTPException(429,'Too many attempts. Try again later.')
        u=db.query(User).filter_by(email=email).first()
        if not u or not u.password_hash or not pass_check(data.password,u.password_hash):
            db.add(LoginFailure(email=email,ip_hash=ip));db.commit()
            raise HTTPException(401,'Incorrect login details')
        db.query(LoginFailure).filter_by(email=email).delete()
        raw,csrf=new_session(db,u);set_session_cookie(response,raw)
        return {'user':basic_user(u),'csrf':csrf}
@app.get('/api/me')
def me(request:Request):
    with Session() as db:
        u,s=auth(request,db);return {'user':basic_user(u),'csrf':s.csrf}
@app.post('/api/logout')
def logout(request:Request,response:Response):
    with Session() as db:
        u,s=auth(request,db,True);db.delete(s);db.commit()
    response.delete_cookie('ss_session');return {'ok':True}

# Google OAuth uses OpenID Connect and verifies Google's signed ID token.
@app.get('/auth/google')
def google_start(role:str='student'):
    cid=os.getenv('GOOGLE_CLIENT_ID');secret=os.getenv('GOOGLE_CLIENT_SECRET');redirect=os.getenv('GOOGLE_REDIRECT_URI')
    if not cid or not secret or not redirect:raise HTTPException(503,'Google sign-in requires OAuth setup in the host account')
    if role not in ('student','teacher','parent'): role='student'
    state=secrets.token_urlsafe(36)
    with Session() as db:
        db.add(OAuthState(state_hash=hashlib.sha256(state.encode()).hexdigest(),role=role,expiry=utcnow()+timedelta(minutes=10)));db.commit()
    url='https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':cid,'redirect_uri':redirect,'response_type':'code','scope':'openid email profile','state':state,'prompt':'select_account'})
    response=RedirectResponse(url)
    response.set_cookie('ss_oauth_state',hashlib.sha256(state.encode()).hexdigest(),max_age=600,httponly=True,secure=COOKIE_SECURE,samesite='lax')
    return response
@app.get('/auth/google/callback')
def google_callback(request:Request,code:str='',state:str='',error:str=''):
    if error:return RedirectResponse('/?login_error=google')
    if not state or not hmac.compare_digest(request.cookies.get('ss_oauth_state',''),hashlib.sha256(state.encode()).hexdigest()):
        raise HTTPException(400,'Google sign-in session could not be verified')
    with Session() as db:
        st=db.get(OAuthState,hashlib.sha256(state.encode()).hexdigest())
        if not st or st.expiry<utcnow():raise HTTPException(400,'Expired Google sign-in. Retry.')
        role=st.role;db.delete(st);db.commit()
    try:
        with httpx.Client(timeout=15) as client:
            t=client.post('https://oauth2.googleapis.com/token',data={'client_id':os.environ['GOOGLE_CLIENT_ID'],'client_secret':os.environ['GOOGLE_CLIENT_SECRET'],'code':code,'grant_type':'authorization_code','redirect_uri':os.environ['GOOGLE_REDIRECT_URI']});t.raise_for_status()
            token=t.json()['id_token']
            jwks=client.get('https://www.googleapis.com/oauth2/v3/certs');jwks.raise_for_status()
        # PyJWT uses Google's published RSA JWKS to validate the signature and audience.
        key=jwt.PyJWKSet.from_dict(jwks.json());header=jwt.get_unverified_header(token)
        k=next((v.key for v in key.keys if v.key_id==header['kid']),None)
        if not k:raise ValueError('Missing Google signing key')
        claims=jwt.decode(token,k,algorithms=['RS256'],audience=os.environ['GOOGLE_CLIENT_ID'],issuer=['accounts.google.com','https://accounts.google.com'])
        if claims.get('email_verified') is not True:raise ValueError('Google email not verified')
    except Exception:
        raise HTTPException(401,'Google sign-in could not be verified')
    email=normemail(claims['email']);sub=claims['sub']
    with Session() as db:
        u=db.query(User).filter_by(google_sub=sub).first()
        if not u:
            u=db.query(User).filter_by(email=email).first()
            if u and u.google_sub and u.google_sub!=sub:raise HTTPException(409,'Account already linked to a different Google identity')
            if u:
                # Linking an existing email account using a verified Google identity.
                u.google_sub=sub
            else:
                u=User(email=email,name=claims.get('name') or email.split('@')[0],role=role,google_sub=sub);db.add(u)
            db.commit()
        raw,csrf=new_session(db,u)
        resp=RedirectResponse('/',status_code=302);set_session_cookie(resp,raw);resp.delete_cookie('ss_oauth_state');return resp

class NewClass(BaseModel):
    subject:str;name:str='';school:str='';grade:str='Class 10'
@app.get('/api/classes')
def list_classes(request:Request):
    with Session() as db:
        u,s=auth(request,db)
        if u.role=='teacher': classes=db.query(Classroom).filter_by(teacher_id=u.id).order_by(Classroom.created.desc()).all()
        elif u.role=='student':classes=db.query(Classroom).join(Enrollment,Enrollment.class_id==Classroom.id).filter(Enrollment.student_id==u.id).order_by(Classroom.subject).all()
        else:classes=[]
        return {'classes':[class_info(c,db,u) for c in classes]}
@app.post('/api/classes')
def create_class(data:NewClass,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'teacher')
        if not u.teacher_verified:raise HTTPException(403,'Teacher account requires administrator verification before creating classes')
        if not data.subject.strip():raise HTTPException(400,'Subject is required')
        for _ in range(7):
            code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(7))
            if not db.query(Classroom).filter_by(code=code).first():break
        c=Classroom(teacher_id=u.id,subject=data.subject.strip()[:70],school=data.school.strip()[:200],grade=data.grade.strip()[:70],name=(data.name.strip() or data.subject.strip()+' Class')[:160],code=code)
        db.add(c);db.commit();return class_info(c,db,u)
class JoinClass(BaseModel):code:str
@app.post('/api/classes/join')
def join_class(data:JoinClass,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student')
        c=db.query(Classroom).filter_by(code=data.code.strip().upper()).first()
        if not c:raise HTTPException(404,'Invalid class code')
        if c.suspended:raise HTTPException(403,'Class is not accepting new students')
        e=db.query(Enrollment).filter_by(class_id=c.id,student_id=u.id).first()
        if not e:e=Enrollment(class_id=c.id,student_id=u.id);db.add(e);db.commit()
        return {'class':class_info(c,db,u),'status':e.status}
@app.get('/api/classes/{cid}')
def class_detail(cid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db);c=get_class_access(db,u,cid)
        assignments=db.query(Assignment).filter_by(class_id=cid).order_by(Assignment.created.desc()).all()
        payload={'class':class_info(c,db,u),'assignments':[assign_info(a,db,u) for a in assignments]}
        if u.role=='teacher':
            es=db.query(Enrollment).filter_by(class_id=cid).all()
            payload['members']=[{'id':e.id,'student_id':e.student_id,'name':db.get(User,e.student_id).name,'email':db.get(User,e.student_id).email,'status':e.status} for e in es]
        return payload
@app.post('/api/classes/{cid}/members/{student_id}/approve')
def approve_member(cid:int,student_id:int,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);c=db.get(Classroom,cid)
        if not c or c.teacher_id!=u.id:raise HTTPException(403,'Only class teacher can approve')
        e=db.query(Enrollment).filter_by(class_id=cid,student_id=student_id).first()
        if not e:raise HTTPException(404,'Request not found')
        e.status='approved';db.commit();return {'ok':True}
class AttendanceInput(BaseModel):
    day:str;records:list[dict]
@app.get('/api/classes/{cid}/attendance')
def class_attendance(cid:int,request:Request,day:str=''):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'teacher');c=db.get(Classroom,cid)
        if not c or c.teacher_id!=u.id:raise HTTPException(403,'Not your class')
        day=day or utcnow().date().isoformat()
        records=db.query(Attendance).filter_by(class_id=cid,day=day).all()
        status={v.student_id:v.status for v in records}
        members=db.query(Enrollment).filter_by(class_id=cid,status='approved').all()
        return {'day':day,'members':[{'id':m.student_id,'name':db.get(User,m.student_id).name,'status':status.get(m.student_id,'not recorded')} for m in members]}
@app.post('/api/classes/{cid}/attendance')
def save_attendance(cid:int,data:AttendanceInput,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'teacher');c=db.get(Classroom,cid)
        if not c or c.teacher_id!=u.id:raise HTTPException(403,'Not your class')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',data.day):raise HTTPException(400,'Invalid date')
        for record in data.records[:300]:
            sid=record.get('student_id');status=record.get('status')
            if status not in ('present','absent','late','excused'):continue
            if not isinstance(sid,int) or not db.query(Enrollment).filter_by(class_id=cid,student_id=sid,status='approved').first():continue
            a=db.query(Attendance).filter_by(class_id=cid,student_id=sid,day=data.day).first()
            if not a:a=Attendance(class_id=cid,student_id=sid,day=data.day);db.add(a)
            a.status=status
        db.commit();return {'ok':True}

@app.post('/api/classes/{cid}/members/{student_id}/remove')
def remove_member(cid:int,student_id:int,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);c=db.get(Classroom,cid)
        if not c or c.teacher_id!=u.id:raise HTTPException(403,'Only class teacher can remove')
        e=db.query(Enrollment).filter_by(class_id=cid,student_id=student_id).first()
        if e:db.delete(e);db.commit()
        return {'ok':True}

def assign_info(a,db,u=None):
    d={'id':a.id,'class_id':a.class_id,'title':a.title,'instructions':a.instructions,'due_date':a.due_date,'kind':a.kind,'max_score':a.max_score,'created':iso(a.created),'question_count':len(json.loads(a.questions or '[]')),'has_file':bool(a.storage_key),'filename':a.original_filename}
    if u and u.role=='student':
        sub=db.query(Submission).filter_by(assignment_id=a.id,student_id=u.id).first()
        d['submission']=sub_info(sub) if sub else None
    return d
class NewAssignment(BaseModel):
    title:str;instructions:str='';due_date:str='';kind:str='worksheet';max_score:float=100;questions:list=Field(default_factory=list)
@app.post('/api/classes/{cid}/assignments')
def new_assignment(cid:int,data:NewAssignment,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'teacher');c=db.get(Classroom,cid)
        if not c or c.teacher_id!=u.id:raise HTTPException(403,'Not your class')
        if not data.title.strip():raise HTTPException(400,'Add title')
        if data.kind not in ('worksheet','quiz','mock'):raise HTTPException(400,'Invalid type')
        if data.due_date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}',data.due_date):raise HTTPException(400,'Invalid date')
        questions=[]
        for q in data.questions[:60]:
            if not isinstance(q,dict):continue
            opts=q.get('options',[])
            if not q.get('question') or not isinstance(opts,list) or len(opts)!=4 or not isinstance(q.get('answer'),int) or q['answer'] not in range(4):continue
            questions.append({'question':str(q['question'])[:600],'options':[str(x)[:260] for x in opts],'answer':q['answer'],'marks':1})
        if data.kind in ('quiz','mock') and not questions:raise HTTPException(400,'Add at least one complete MCQ')
        a=Assignment(class_id=cid,title=data.title.strip()[:200],instructions=data.instructions[:8000],due_date=data.due_date,kind=data.kind,max_score=(len(questions) if questions else max(1,min(1000,data.max_score))),questions=json.dumps(questions));db.add(a);db.commit()
        return assign_info(a,db,u)
@app.post('/api/assignments/{aid}/attach')
async def attach_assignment(aid:int,request:Request,upload:UploadFile=File(...)):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'teacher')
        a=db.get(Assignment,aid)
        if not a or db.get(Classroom,a.class_id).teacher_id!=u.id:raise HTTPException(403,'Not your assignment')
        key=write_upload(upload)
        if a.storage_key:(FILES/a.storage_key).unlink(missing_ok=True)
        a.storage_key=key;a.original_filename=Path(upload.filename).name[:255];a.mime=upload.content_type
        db.commit();return {'ok':True,'filename':a.original_filename}
@app.get('/api/assignments/{aid}/file')
def assignment_file(aid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db);a,c=assignment_access(db,u,aid)
        if not a.storage_key:raise HTTPException(404,'No worksheet attached')
        return FileResponse(FILES/a.storage_key,media_type=a.mime,filename=a.original_filename,content_disposition_type='inline')

@app.get('/api/assignments/{aid}')
def assignment_detail(aid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db);a,c=assignment_access(db,u,aid);d=assign_info(a,db,u)
        questions=json.loads(a.questions or '[]')
        if u.role=='teacher':d['questions']=questions
        else:d['questions']=[{'question':q['question'],'options':q['options'],'marks':q.get('marks',1)} for q in questions]
        if u.role=='teacher':
            sub=db.query(Submission).filter_by(assignment_id=a.id).order_by(Submission.submitted.desc()).all()
            d['submissions']=[dict(sub_info(x),student_name=db.get(User,x.student_id).name) for x in sub]
        return d

def sub_info(sub):
    return {'id':sub.id,'student_id':sub.student_id,'notes':sub.notes,'filename':sub.original_filename,'has_file':bool(sub.storage_key),'mime':sub.mime,'score':sub.score,'feedback':sub.feedback,'status':sub.status,'submitted':iso(sub.submitted),'strokes':json.loads(sub.strokes or '[]')}
@app.post('/api/assignments/{aid}/submit')
async def submit_file(aid:int,request:Request,notes:str=Form(''),upload:UploadFile|None=File(None)):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student');a,c=assignment_access(db,u,aid)
        if a.kind in ('quiz','mock'):raise HTTPException(400,'Use the online quiz form')
        sub=db.query(Submission).filter_by(assignment_id=aid,student_id=u.id).first()
        if not sub:sub=Submission(assignment_id=aid,student_id=u.id);db.add(sub)
        if upload and upload.filename:
            key=write_upload(upload)
            if sub.storage_key:(FILES/sub.storage_key).unlink(missing_ok=True)
            sub.storage_key=key;sub.original_filename=Path(upload.filename).name[:255];sub.mime=upload.content_type
        sub.notes=notes[:4000];sub.status='submitted';sub.submitted=utcnow();sub.feedback='';sub.score=None;sub.strokes='[]'
        db.commit();return sub_info(sub)
class Attempt(BaseModel):answers:list[int|None]
@app.post('/api/assignments/{aid}/attempt')
def quiz_attempt(aid:int,data:Attempt,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student');a,c=assignment_access(db,u,aid)
        if a.kind not in ('quiz','mock'):raise HTTPException(400,'Not a quiz')
        qs=json.loads(a.questions or '[]')
        if len(data.answers)!=len(qs):raise HTTPException(400,'Answer each question (or leave blank)')
        correct=sum(1 for q,x in zip(qs,data.answers) if x==q['answer'])
        sub=db.query(Submission).filter_by(assignment_id=aid,student_id=u.id).first()
        if not sub:sub=Submission(assignment_id=aid,student_id=u.id);db.add(sub)
        sub.answers=json.dumps(data.answers);sub.score=correct;sub.feedback=f'Automatically marked: {correct} of {len(qs)} correct.';sub.status='returned';sub.submitted=utcnow();db.commit()
        return {'score':correct,'total':len(qs),'correct_answers':[q['answer'] for q in qs],'submission':sub_info(sub)}
@app.get('/api/submissions/{sid}')
def submission_detail(sid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db);sub=db.get(Submission,sid)
        if not sub or not file_perm(db,u,sub):raise HTTPException(403,'Not permitted')
        return sub_info(sub)
@app.get('/api/submissions/{sid}/file')
def submission_file(sid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db);sub=db.get(Submission,sid)
        if not sub or not file_perm(db,u,sub) or not sub.storage_key:raise HTTPException(403,'Not permitted')
        return FileResponse(FILES/sub.storage_key,media_type=sub.mime,filename=sub.original_filename,content_disposition_type='inline')
class Mark(BaseModel):score:float;feedback:str='';strokes:list=Field(default_factory=list)
@app.post('/api/submissions/{sid}/mark')
def mark_submission(sid:int,data:Mark,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'teacher');sub=db.get(Submission,sid)
        if not sub:raise HTTPException(404,'Not found')
        a=db.get(Assignment,sub.assignment_id);c=db.get(Classroom,a.class_id)
        if c.teacher_id!=u.id:raise HTTPException(403,'Not your class')
        if data.score<0 or data.score>a.max_score:raise HTTPException(400,'Score out of range')
        strokes=[]
        for stroke in data.strokes[:500]:
            if not isinstance(stroke,list):continue
            pts=[]
            for p in stroke[:500]:
                if isinstance(p,list) and len(p)==2 and all(isinstance(v,(int,float)) and 0<=v<=1 for v in p):pts.append([round(p[0],4),round(p[1],4)])
            if pts:strokes.append(pts)
        sub.score=data.score;sub.feedback=data.feedback[:4000];sub.strokes=json.dumps(strokes);sub.status='returned';db.commit();return sub_info(sub)
class NewComment(BaseModel):student_id:int|None=None;body:str
@app.get('/api/assignments/{aid}/comments')
def comments(aid:int,request:Request,student_id:int|None=None):
    with Session() as db:
        u,s=auth(request,db);a,c=assignment_access(db,u,aid)
        target=u.id if u.role=='student' else student_id
        if not target:raise HTTPException(400,'Select a student')
        if u.role=='teacher' and not db.query(Enrollment).filter_by(class_id=c.id,student_id=target,status='approved').first():raise HTTPException(403,'Student not in your class')
        cs=db.query(Comment).filter_by(assignment_id=aid,student_id=target).order_by(Comment.created).all()
        return {'comments':[{'id':v.id,'author':db.get(User,v.author_id).name,'author_role':db.get(User,v.author_id).role,'body':v.body,'created':iso(v.created)} for v in cs]}
@app.post('/api/assignments/{aid}/comments')
def add_comment(aid:int,data:NewComment,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);a,c=assignment_access(db,u,aid)
        target=u.id if u.role=='student' else data.student_id
        if not target or not db.query(Enrollment).filter_by(class_id=c.id,student_id=target,status='approved').first():raise HTTPException(403,'Not an enrolled student')
        if not data.body.strip():raise HTTPException(400,'Write a message')
        v=Comment(assignment_id=aid,student_id=target,author_id=u.id,body=data.body.strip()[:2500]);db.add(v);db.commit();return {'ok':True}

class LinkRequest(BaseModel):student_email:str
@app.post('/api/parent/request')
def parent_request(data:LinkRequest,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'parent')
        student=db.query(User).filter_by(email=normemail(data.student_email),role='student').first()
        if not student:raise HTTPException(404,'Student account not found')
        link=db.query(ParentLink).filter_by(parent_id=u.id,student_id=student.id).first()
        if not link:link=ParentLink(parent_id=u.id,student_id=student.id);db.add(link);db.commit()
        return {'status':link.status}
@app.get('/api/parent/pending')
def parent_pending(request:Request):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'student')
        ls=db.query(ParentLink).filter_by(student_id=u.id,status='pending').all()
        return {'requests':[{'id':v.id,'parent':db.get(User,v.parent_id).name,'email':db.get(User,v.parent_id).email} for v in ls]}
@app.post('/api/parent/links/{lid}/{decision}')
def parent_decide(lid:int,decision:str,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student');l=db.get(ParentLink,lid)
        if not l or l.student_id!=u.id:raise HTTPException(403,'Not your request')
        if decision not in ('approve','reject'):raise HTTPException(400,'Invalid decision')
        l.status='approved' if decision=='approve' else 'rejected';db.commit();return {'ok':True}
@app.get('/api/parent/children')
def parent_children(request:Request):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'parent')
        ls=db.query(ParentLink).filter_by(parent_id=u.id).all()
        return {'children':[dict(basic_user(db.get(User,l.student_id)),link_status=l.status) for l in ls]}

class TaskInput(BaseModel):day:str;subject:str='General';timing:str='';title:str
@app.get('/api/planner')
def planner(request:Request,day:str=''):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'student')
        if not day:day=utcnow().date().isoformat()
        ts=db.query(PlannerTask).filter_by(student_id=u.id,day=day).order_by(PlannerTask.id).all()
        details={}
        if (BASE/'planner_data.json').exists():
            src=json.loads((BASE/'planner_data.json').read_text(encoding='utf-8'));details=src.get('days',{}).get(day,{})
        return {'day':day,'tasks':[task_info(t) for t in ts], 'template_note':details.get('kind','')}
def task_info(t):return {'id':t.id,'day':t.day,'subject':t.subject,'time':t.timing,'title':t.title,'done':t.completed,'source':t.source}
@app.post('/api/planner')
def add_task(data:TaskInput,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',data.day):raise HTTPException(400,'Invalid date')
        if not data.title.strip():raise HTTPException(400,'Add a task')
        t=PlannerTask(student_id=u.id,day=data.day,subject=data.subject[:75],timing=data.timing[:80],title=data.title.strip()[:600]);db.add(t);db.commit();return task_info(t)
@app.post('/api/planner/{tid}/toggle')
def toggle_task(tid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student')
        t=db.get(PlannerTask,tid)
        if not t or t.student_id!=u.id:raise HTTPException(403,'Not your task')
        t.completed=not t.completed;db.commit();return task_info(t)
@app.post('/api/planner/import-cbse')
def import_cbse(request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student')
        path=BASE/'planner_data.json'
        if not path.exists():raise HTTPException(404,'CBSE planner template unavailable')
        src=json.loads(path.read_text(encoding='utf-8'))
        existing=db.query(PlannerTask).filter_by(student_id=u.id,source='cbse2027').count()
        if existing:return {'added':0,'message':'CBSE template already imported; your existing checks are preserved'}
        n=0
        for day,p in src['days'].items():
            for session in p.get('sessions',[]):
                title=session.get('text','');subject=title.split(':',1)[0].strip()[:75]
                db.add(PlannerTask(student_id=u.id,day=day,subject=subject,timing=session.get('time','')[:80],title=title[:600],source='cbse2027'));n+=1
        for subject,names in src.get('chapters',{}).items():
            for name in names:
                db.add(ChapterCheck(student_id=u.id,subject=subject,title=name))
        db.commit();return {'added':n,'chapters':sum(map(len,src.get('chapters',{}).values()))}
@app.get('/api/exams')
def exams(request:Request):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'student')
        if not db.query(PlannerTask).filter_by(student_id=u.id,source='cbse2027').first():return {'exams':[]}
        file=BASE/'planner_data.json'
        return {'exams':json.loads(file.read_text(encoding='utf-8')).get('exams',[]) if file.exists() else []}

@app.get('/api/chapters')
def chapters(request:Request):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'student')
        cs=db.query(ChapterCheck).filter_by(student_id=u.id).all()
        return {'chapters':[{'id':c.id,'subject':c.subject,'title':c.title,'studied':c.studied,'revised':c.revised} for c in cs]}
class ChapterChange(BaseModel):field:str
@app.post('/api/chapters/{id}/toggle')
def chapter_toggle(id:int,data:ChapterChange,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'student');c=db.get(ChapterCheck,id)
        if not c or c.student_id!=u.id:raise HTTPException(403,'Not your chapter')
        if data.field not in ('studied','revised'):raise HTTPException(400,'Invalid field')
        setattr(c,data.field,not getattr(c,data.field));db.commit();return {'ok':True}

@app.get('/api/reports/{student_id}')
def report(student_id:int,request:Request):
    with Session() as db:
        u,s=auth(request,db)
        if not student_access(db,u,student_id):raise HTTPException(403,'No access to this student')
        return student_report(db,student_id,u)
def student_report(db,sid,actor=None):
    student=db.get(User,sid)
    teacher_scope=actor is not None and actor.role=='teacher'
    tasks=[] if teacher_scope else db.query(PlannerTask).filter_by(student_id=sid).all()
    cs=[] if teacher_scope else db.query(ChapterCheck).filter_by(student_id=sid).all()
    enrolls=db.query(Enrollment).filter_by(student_id=sid,status='approved').all()
    if teacher_scope: enrolls=[en for en in enrolls if db.get(Classroom,en.class_id).teacher_id==actor.id]
    aids=[a.id for e in enrolls for a in db.query(Assignment).filter_by(class_id=e.class_id).all()]
    subs=db.query(Submission).filter_by(student_id=sid).all()
    scores=[];completed_a=0
    for sub in subs:
        if sub.assignment_id in aids:
            completed_a+=1
            a=db.get(Assignment,sub.assignment_id)
            if sub.score is not None and a.max_score:scores.append(round(sub.score/a.max_score*100,1))
    subjects={}
    for t in tasks:
        v=subjects.setdefault(t.subject,{'total':0,'done':0,'chapters':0,'revised':0});v['total']+=1;v['done']+=int(t.completed)
    for c in cs:
        v=subjects.setdefault(c.subject,{'total':0,'done':0,'chapters':0,'revised':0});v['chapters']+=1;v['revised']+=int(c.revised)
    # Count classroom assignments in the student's subject-level progress.
    submissions_by_a={v.assignment_id:v for v in subs}
    for enrollment in enrolls:
        cl=db.get(Classroom,enrollment.class_id)
        v=subjects.setdefault(cl.subject,{'total':0,'done':0,'chapters':0,'revised':0})
        for assignment in db.query(Assignment).filter_by(class_id=cl.id).all():
            v['total']+=1;v['done']+=int(assignment.id in submissions_by_a)
    if not teacher_scope and student.grade.lower().replace(' ','') in ('class10','grade10','10'):
        for name in ['English','Hindi','Maths','Science','Social','AI']:
            subjects.setdefault(name,{'total':0,'done':0,'chapters':0,'revised':0})
    class_ids=[en.class_id for en in enrolls]
    attendance=db.query(Attendance).filter(Attendance.student_id==sid,Attendance.class_id.in_(class_ids)).all() if class_ids else []
    return {'student':{'id':student.id,'name':student.name,'grade':student.grade},'attendance_total':len(attendance),'attendance_present':sum(a.status in ('present','late') for a in attendance),'attendance_absent':sum(a.status=='absent' for a in attendance),'tasks_total':len(tasks),'tasks_done':sum(t.completed for t in tasks),
        'assignments_total':len(aids),'assignments_submitted':completed_a,'average_score':round(sum(scores)/len(scores),1) if scores else None,
        'classes_count':len(enrolls),'chapters_total':len(cs),'chapters_revised':sum(c.revised for c in cs),'subjects':subjects}
@app.get('/api/dashboard')
def dashboard(request:Request):
    with Session() as db:
        u,s=auth(request,db)
        if u.role=='student':
            es=db.query(Enrollment).filter_by(student_id=u.id,status='approved').all();aids=[a.id for e in es for a in db.query(Assignment).filter_by(class_id=e.class_id).all()]
            assignments=[assign_info(db.get(Assignment,aid),db,u) for aid in aids if db.get(Assignment,aid)]
            return {'role':u.role,'report':student_report(db,u.id,u),'upcoming':sorted(assignments,key=lambda x:x['due_date'] or '9999-99-99')[:8]}
        if u.role=='teacher':
            classes=db.query(Classroom).filter_by(teacher_id=u.id).all();ids=[c.id for c in classes]
            assignments=db.query(Assignment).filter(Assignment.class_id.in_(ids)).all() if ids else []
            return {'role':u.role,'teacher_verified':bool(u.teacher_verified),'classes':len(classes),'students':len({e.student_id for e in db.query(Enrollment).filter(Enrollment.class_id.in_(ids),Enrollment.status=='approved').all()}) if ids else 0,'assignments':len(assignments),'pending_joins':db.query(Enrollment).filter(Enrollment.class_id.in_(ids),Enrollment.status=='pending').count() if ids else 0}
        if u.role=='parent':
            ls=db.query(ParentLink).filter_by(parent_id=u.id,status='approved').all()
            return {'role':u.role,'children':[student_report(db,l.student_id,u) for l in ls]}
        return {'role':'admin','users':db.query(User).count(),'classes':db.query(Classroom).count(),'papers':db.query(Paper).count(),'pending_papers':db.query(Paper).filter_by(verified=False).count()}

@app.get('/api/library')
def library(request:Request):
    with Session() as db:
        u,s=auth(request,db)
        papers=db.query(Paper).order_by(Paper.year.desc(),Paper.subject,Paper.created.desc()).all()
        if u.role!='admin':papers=[p for p in papers if p.verified]
        return {'papers':[{'id':p.id,'title':p.title,'subject':p.subject,'year':p.year,'board':p.board,'grade':p.grade,'set':p.set_no,'verified':p.verified,'has_file':bool(p.storage_key),'source_url':p.source_url} for p in papers]}
@app.post('/api/library')
async def add_paper(request:Request,title:str=Form(...),subject:str=Form(...),year:int=Form(...),set_no:str=Form(''),board:str=Form('CBSE'),grade:str=Form('Class 10'),source_url:str=Form(''),upload:UploadFile|None=File(None)):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'admin')
        if not 2000<=year<=2100:raise HTTPException(400,'Invalid year')
        if source_url and not source_url.startswith('https://'):raise HTTPException(400,'Source URL must start with https://')
        key=write_upload(upload) if upload and upload.filename else ''
        if not key and not source_url.startswith('https://'):raise HTTPException(400,'Upload a paper or supply a verified HTTPS source link')
        p=Paper(uploaded_by=u.id,title=title[:200],subject=subject[:90],year=year,set_no=set_no[:30],board=board[:100],grade=grade[:70],source_url=source_url[:900],storage_key=key,original_filename=(upload.filename[:255] if upload else ''),mime=upload.content_type if upload else '',verified=False)
        db.add(p);db.commit();return {'id':p.id,'verified':False}
@app.post('/api/library/{pid}/verify')
def verify_paper(pid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'admin');p=db.get(Paper,pid)
        if not p:raise HTTPException(404,'Paper not found')
        p.verified=True;db.commit();return {'ok':True}
@app.get('/api/library/{pid}/file')
def paper_file(pid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db);p=db.get(Paper,pid)
        if not p or not file_perm(db,u,p) or not p.storage_key:raise HTTPException(403,'File unavailable')
        return FileResponse(FILES/p.storage_key,media_type=p.mime,filename=p.original_filename,content_disposition_type='inline')
@app.post('/api/admin/users/{uid}/verify-teacher')
def verify_teacher(uid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'admin')
        teacher=db.get(User,uid)
        if not teacher or teacher.role!='teacher':raise HTTPException(404,'Teacher not found')
        teacher.teacher_verified=True;db.commit();return {'ok':True}

@app.get('/api/admin/classes')
def admin_classes(request:Request):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'admin')
        classes=db.query(Classroom).order_by(Classroom.created.desc()).limit(500).all()
        return {'classes':[class_info(c,db,u) for c in classes]}
@app.post('/api/admin/classes/{cid}/toggle-suspend')
def admin_suspend_class(cid:int,request:Request):
    with Session() as db:
        u,s=auth(request,db,True);require_role(u,'admin');c=db.get(Classroom,cid)
        if not c:raise HTTPException(404,'Class not found')
        c.suspended=not c.suspended;db.commit();return {'suspended':bool(c.suspended)}

@app.get('/api/admin/users')
def admin_users(request:Request):
    with Session() as db:
        u,s=auth(request,db);require_role(u,'admin')
        return {'users':[basic_user(v) for v in db.query(User).order_by(User.created.desc()).limit(300).all()]}

# Bootstrap admin only with operator-supplied environment values. NEVER use public demo credentials in production.
def init_admin():
    email=normemail(os.getenv('BOOTSTRAP_ADMIN_EMAIL',''))
    pw=os.getenv('BOOTSTRAP_ADMIN_PASSWORD','')
    if not email or not pw:return
    if len(pw)<12:raise RuntimeError('BOOTSTRAP_ADMIN_PASSWORD must have at least 12 characters')
    with Session() as db:
        if not db.query(User).filter_by(email=email).first():
            db.add(User(email=email,name=os.getenv('BOOTSTRAP_ADMIN_NAME','Platform Admin'),role='admin',password_hash=pass_hash(pw)));db.commit()
init_admin()
