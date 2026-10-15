import json, os, pathlib, secrets, shutil, socket, subprocess
import psycopg2
from psycopg2 import sql

class DemoDatabase:
 def __init__(self,tmp):
  self.tmp=tmp;self.data=tmp/'external-pg';self.bin=os.environ.get('PG_BINDIR') or str(pathlib.Path(shutil.which('initdb') or '/missing/initdb').parent);self.bin=self.bin.rstrip('/')+'/'
  with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
  self.user='dashboard_reader';self.password=secrets.token_hex(24);self.name='dashboard_demo'
 def setup(self):
  subprocess.run([self.bin+'initdb','-D',str(self.data),'-A','trust','--no-locale','--encoding=UTF8'],capture_output=True,check=True)
  (self.data/'pg_hba.conf').write_text('local all all trust\nhost all all 127.0.0.1/32 scram-sha-256\n')
  subprocess.run([self.bin+'pg_ctl','-D',str(self.data),'-l',str(self.tmp/'external-pg.log'),'-o',f'-h 127.0.0.1 -p {self.port} -k {self.tmp}','-w','start'],capture_output=True,check=True)
  with self.admin('postgres') as c:
   c.autocommit=True
   with c.cursor() as q:
    q.execute('SET password_encryption = %s',('scram-sha-256',))
    q.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(self.user),sql.Literal(self.password)))
  c=self.admin('postgres');c.autocommit=True;c.cursor().execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.name)));c.close()
  with self.admin() as c:
   with c.cursor() as q:
    q.execute('CREATE TABLE inventory (id text PRIMARY KEY, name text, price numeric(10,2), currency text)')
    q.executemany('INSERT INTO inventory VALUES (%s,%s,%s,%s)',[('mug','Blue mug',12.5,'USD'),('lamp','Desk lamp',45,'USD'),('book','Notebook',8.75,'USD')])
    q.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(self.name),sql.Identifier(self.user)))
    q.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(self.user)))
    q.execute(sql.SQL('GRANT SELECT ON inventory TO {}').format(sql.Identifier(self.user)))
  creds={'port':self.port,'secrets':{'PG_USER':self.user,'PG_PASSWORD':self.password,'PG_DATABASE':self.name}}
  self.path=self.tmp/'demo-credentials.json';self.path.write_text(json.dumps(creds));self.path.chmod(0o600)
  # Validate both real authentication and the read-only database role.
  try:
   psycopg2.connect(host='127.0.0.1',port=self.port,user=self.user,password='incorrect',dbname=self.name)
   raise AssertionError('Incorrect password accepted')
  except psycopg2.OperationalError:pass
  with psycopg2.connect(host='127.0.0.1',port=self.port,user=self.user,password=self.password,dbname=self.name) as c:
   with c.cursor() as q:
    q.execute('SELECT count(*) FROM inventory');assert q.fetchone()[0]==3
    try:q.execute("DELETE FROM inventory");raise AssertionError('Reader can write')
    except psycopg2.errors.InsufficientPrivilege:pass
 def admin(self,dbname=None):return psycopg2.connect(host=str(self.tmp),port=self.port,dbname=dbname or self.name)
 def stop(self):
  subprocess.run([self.bin+'pg_ctl','-D',str(self.data),'-m','fast','-w','stop'],capture_output=True)
  if hasattr(self,'path'):self.path.unlink(missing_ok=True)
