"""Integration smoke tests. Run with a fresh local database or a disposable test installation."""
import os, secrets, io, base64
os.environ.setdefault('BOOTSTRAP_ADMIN_EMAIL','admin-test@example.org')
os.environ.setdefault('BOOTSTRAP_ADMIN_PASSWORD','StrongTest1234!')
from fastapi.testclient import TestClient
from app import app

def client(role):
    c=TestClient(app)
    data={'email':f'{role}{secrets.token_hex(4)}@example.test','password':'StrongTest1234!','name':role.title(),'role':role,'school':role+' School'}
    r=c.post('/api/register',json=data); assert r.status_code==200,r.text
    return c,data,r.json()['csrf']

def post(c,path,csrf,data=None,files=None):
    if files is not None:r=c.post(path,data=data or {},files=files,headers={'x-csrf':csrf})
    else:r=c.post(path,json=data,headers={'x-csrf':csrf})
    assert r.status_code==200,(path,r.status_code,r.text)
    return r.json()

if __name__=='__main__':
    teacher,t,tc=client('teacher'); student,s,sc=client('student'); outsider,_,oc=client('student'); parent,p,pc=client('parent')
    admin=TestClient(app)
    ar=admin.post('/api/login',json={'email':os.environ['BOOTSTRAP_ADMIN_EMAIL'],'password':os.environ['BOOTSTRAP_ADMIN_PASSWORD']})
    assert ar.status_code==200,ar.text;ac=ar.json()['csrf']
    # signup cannot choose admin
    response=TestClient(app).post('/api/register',json={'email':'bad@example.test','password':'strongpassword123','name':'Bad','role':'admin'})
    assert response.status_code==403
    teacherid=teacher.get('/api/me').json()['user']['id']
    post(admin,f'/api/admin/users/{teacherid}/verify-teacher',ac,{})
    cls=post(teacher,'/api/classes',tc,{'name':'Maths from DPS','subject':'Maths','school':'DPS','grade':'Class 10'})
    assert len(cls['code'])==7
    joined=post(student,'/api/classes/join',sc,{'code':cls['code']});assert joined['status']=='pending'
    # Pending students must not access class material
    assert student.get('/api/classes/'+str(cls['id'])).status_code==403
    sid=student.get('/api/me').json()['user']['id']; tid=teacher.get('/api/me').json()['user']['id']; oid=outsider.get('/api/me').json()['user']['id']
    post(teacher,f'/api/classes/{cls["id"]}/members/{sid}/approve',tc,{})
    assert student.get('/api/classes/'+str(cls['id'])).status_code==200
    # Teacher records attendance, accessible in approved parent's report.
    post(teacher,f'/api/classes/{cls["id"]}/attendance',tc,{'day':'2026-11-22','records':[{'student_id':sid,'status':'present'}]})
    assert teacher.get(f'/api/classes/{cls["id"]}/attendance?day=2026-11-22').json()['members'][0]['status']=='present'
    assert student.get(f'/api/reports/{sid}').json()['attendance_present']==1
    # teacher worksheet attachment
    assignment=post(teacher,f'/api/classes/{cls["id"]}/assignments',tc,{'title':'Algebra worksheet','instructions':'Answer all questions','kind':'worksheet','due_date':'2026-11-30','max_score':25})
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==')
    attach=post(teacher,f'/api/assignments/{assignment["id"]}/attach',tc,files={'upload':('worksheet.png',png,'image/png')})
    assert attach['ok']
    assert student.get(f'/api/assignments/{assignment["id"]}/file').status_code==200
    assert outsider.get(f'/api/assignments/{assignment["id"]}/file').status_code==403
    submit=post(student,f'/api/assignments/{assignment["id"]}/submit',sc,{'notes':'My answers'},files={'upload':('answer.png',png,'image/png')})
    subid=submit['id']
    assert outsider.get(f'/api/submissions/{subid}/file').status_code==403
    assert teacher.get(f'/api/submissions/{subid}/file').status_code==200
    # private assignment question thread
    post(student,f'/api/assignments/{assignment["id"]}/comments',sc,{'body':'Why is x negative?'})
    assert len(teacher.get(f'/api/assignments/{assignment["id"]}/comments',params={'student_id':sid}).json()['comments'])==1
    assert outsider.get(f'/api/assignments/{assignment["id"]}/comments',params={'student_id':sid}).status_code==403
    post(teacher,f'/api/assignments/{assignment["id"]}/comments',tc,{'student_id':sid,'body':'Check the sign'} )
    assert len(student.get(f'/api/assignments/{assignment["id"]}/comments').json()['comments'])==2
    mark=post(teacher,f'/api/submissions/{subid}/mark',tc,{'score':21,'feedback':'Check Q3','strokes':[[[0.1,0.2],[0.7,0.9]]]})
    assert mark['score']==21 and mark['status']=='returned'
    # auto-mark MCQ, server never exposes answer keys before attempt
    quiz=post(teacher,f'/api/classes/{cls["id"]}/assignments',tc,{'title':'Quick quiz','kind':'quiz','questions':[{'question':'2+2?','options':['1','4','6','8'],'answer':1}]})
    public=student.get(f'/api/assignments/{quiz["id"]}').json()
    assert 'answer' not in public['questions'][0]
    res=post(student,f'/api/assignments/{quiz["id"]}/attempt',sc,{'answers':[1]})
    assert res['score']==1
    # Parent link requires child's explicit approval
    p0=post(parent,'/api/parent/request',pc,{'student_email':s['email']});assert p0['status']=='pending'
    assert len(parent.get('/api/dashboard').json()['children'])==0
    pending=student.get('/api/parent/pending').json()['requests'];assert len(pending)>0
    post(student,f'/api/parent/links/{pending[0]["id"]}/approve',sc,{})
    assert len(parent.get('/api/dashboard').json()['children'])==1
    assert parent.get('/api/reports/'+str(sid)).status_code==200
    assert parent.get('/api/reports/'+str(sid)).json()['attendance_present']==1
    assert outsider.get('/api/reports/'+str(sid)).status_code==403
    # Import optional CBSE template: must not impact others
    imported=post(student,'/api/planner/import-cbse',sc,{})
    assert imported['added']>100,imported
    sample=student.get('/api/planner?day=2026-11-22').json()
    assert sample['tasks'];tid0=sample['tasks'][0]['id']
    toggled=post(student,f'/api/planner/{tid0}/toggle',sc,{})
    assert toggled['done'] is True
    assert not outsider.get('/api/planner?day=2026-11-22').json()['tasks']
    # Admin-only verified shared global library
    # File-multipart request works for admin
    paper=post(admin,'/api/library',ac,{'title':'CBSE Maths authentic set','subject':'Maths','year':'2025','set_no':'30/1/1','source_url':'https://cbse.gov.in/example.pdf'},files={'upload':('paper.png',png,'image/png')})
    assert len(student.get('/api/library').json()['papers'])==0
    post(admin,f'/api/library/{paper["id"]}/verify',ac,{})
    assert len(student.get('/api/library').json()['papers'])==1
    assert student.get(f'/api/library/{paper["id"]}/file').status_code==200
    assert student.post('/api/library',headers={'x-csrf':sc},data={'title':'fake','subject':'Maths','year':'2025','source_url':'https://cbse.gov.in/p.pdf'}).status_code==403
    assert len(admin.get('/api/admin/classes').json()['classes'])>0
    suspended=post(admin,f'/api/admin/classes/{cls["id"]}/toggle-suspend',ac,{})
    assert suspended['suspended']
    assert student.get('/api/classes/'+str(cls['id'])).status_code==403
    print('PASS: 4 roles, cross-school join approval, private comments, worksheet upload, red-pen grading, auto MCQs, guardian approval, CBSE import, global library, role isolation')
