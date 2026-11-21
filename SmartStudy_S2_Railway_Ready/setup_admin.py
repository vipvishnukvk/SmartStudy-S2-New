"""Interactive setup of the initial administrator. Run on the server before public use."""
from getpass import getpass
from app import Session,User,pass_hash,normemail
email=normemail(input('Admin email address: '))
name=input('Administrator name: ').strip() or 'Administrator'
password=getpass('New admin password (12+ characters): ')
if len(password)<12 or '@' not in email:raise SystemExit('Email and a 12+ character password required')
with Session() as db:
    if db.query(User).filter_by(email=email).first():raise SystemExit('Email already registered. No changes were made.')
    db.add(User(email=email,name=name[:120],role='admin',password_hash=pass_hash(password)))
    db.commit()
print('Administrator created. Keep these credentials private.')
