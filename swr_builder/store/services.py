"""服务、配置修订与密钥版本。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .schema import utcnow


class ServicesMixin:
    @staticmethod
    def _revision(row: sqlite3.Row) -> Dict[str, Any]:
        item=dict(row)
        item['spec']=json.loads(item.pop('spec_json'))
        item['secret_versions']=json.loads(item.pop('secret_versions_json'))
        return item

    @staticmethod
    def _service(row: sqlite3.Row) -> Dict[str, Any]:
        item=dict(row)
        item['spec']=json.loads(item.pop('spec_json')) if item.get('spec_json') else None
        item.pop('spec_json',None)
        return item

    def get_service(self, service_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute("SELECT s.*,r.spec_json FROM services s LEFT JOIN service_revisions r ON r.id=s.current_revision_id WHERE s.id=?",(service_id,)).fetchone()
        return self._service(row) if row else None

    def list_services(self, project_ids: Optional[List[int]]=None, namespace_ids: Optional[List[int]]=None) -> List[Dict[str,Any]]:
        where=[];args=[]
        if project_ids is not None or namespace_ids is not None:
            scopes=[]
            if project_ids:
                scopes.append(f"s.project_id IN ({','.join('?' for _ in project_ids)})");args.extend(project_ids)
            if namespace_ids:
                scopes.append(f"s.namespace_id IN ({','.join('?' for _ in namespace_ids)})");args.extend(namespace_ids)
            if not scopes:return []
            where.append('('+' OR '.join(scopes)+')')
        clause=' WHERE '+' AND '.join(where) if where else ''
        with self._db() as db:
            rows=db.execute("SELECT s.*,r.spec_json FROM services s LEFT JOIN service_revisions r ON r.id=s.current_revision_id"+clause+" ORDER BY s.name COLLATE NOCASE",args).fetchall()
        return [self._service(row) for row in rows]

    def create_service(self,name: str,project_id: int,namespace_id: int,app_id: int,spec: Dict[str,Any],created_by: int) -> Dict[str,Any]:
        now=utcnow()
        with self._db() as db:
            app=db.execute('SELECT project_id FROM apps WHERE id=?',(app_id,)).fetchone()
            allocation=db.execute('SELECT 1 FROM project_namespaces WHERE project_id=? AND namespace_id=?',(project_id,namespace_id)).fetchone()
            if not app or app['project_id']!=project_id:raise ValueError('应用不属于指定项目')
            if not allocation:raise ValueError('项目尚未分配目标命名空间')
            service_id=db.execute('INSERT INTO services(name,project_id,namespace_id,app_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                (name,project_id,namespace_id,app_id,now,now)).lastrowid
            revision_id=db.execute('INSERT INTO service_revisions(service_id,number,spec_json,secret_versions_json,created_by,created_at) VALUES(?,1,?,?,?,?)',
                (service_id,json.dumps(spec,ensure_ascii=False),json.dumps({},ensure_ascii=False),created_by,now)).lastrowid
            db.execute('UPDATE services SET current_revision_id=? WHERE id=?',(revision_id,service_id))
        return self.get_service(int(service_id))  # type: ignore[return-value]

    def update_service_revision(self,service_id: int,base_revision: int,spec: Dict[str,Any],created_by: int) -> Optional[Dict[str,Any]]:
        now=utcnow()
        with self._db() as db:
            service=db.execute('SELECT current_revision_id FROM services WHERE id=?',(service_id,)).fetchone()
            if not service:return None
            if service['current_revision_id']!=base_revision:raise RuntimeError('REVISION_CONFLICT')
            previous=db.execute('SELECT secret_versions_json FROM service_revisions WHERE id=?',(base_revision,)).fetchone()
            versions=previous['secret_versions_json'] if previous else '{}'
            number=db.execute('SELECT COALESCE(MAX(number),0)+1 FROM service_revisions WHERE service_id=?',(service_id,)).fetchone()[0]
            revision_id=db.execute('INSERT INTO service_revisions(service_id,number,spec_json,secret_versions_json,created_by,created_at) VALUES(?,?,?,?,?,?)',
                (service_id,number,json.dumps(spec,ensure_ascii=False),versions,created_by,now)).lastrowid
            changed=db.execute('UPDATE services SET current_revision_id=?,updated_at=? WHERE id=? AND current_revision_id=?',
                (revision_id,now,service_id,base_revision)).rowcount
            if not changed:raise RuntimeError('REVISION_CONFLICT')
            row=db.execute('SELECT * FROM service_revisions WHERE id=?',(revision_id,)).fetchone()
        return self.get_service(service_id),self._revision(row)

    def get_revision(self,service_id: int,revision_id: int) -> Optional[Dict[str,Any]]:
        with self._db() as db:
            row=db.execute('SELECT * FROM service_revisions WHERE id=? AND service_id=?',(revision_id,service_id)).fetchone()
        return self._revision(row) if row else None

    def list_revisions(self,service_id: int) -> List[Dict[str,Any]]:
        with self._db() as db:
            rows=db.execute('SELECT * FROM service_revisions WHERE service_id=? ORDER BY number DESC',(service_id,)).fetchall()
        return [self._revision(row) for row in rows]

    def create_secret_revision(self,service_id: int,key: str,ciphertext: str,created_by: int) -> Optional[Dict[str,Any]]:
        now=utcnow()
        with self._db() as db:
            service=db.execute('SELECT current_revision_id FROM services WHERE id=?',(service_id,)).fetchone()
            if not service:return None
            current=db.execute('SELECT * FROM service_revisions WHERE id=?',(service['current_revision_id'],)).fetchone()
            versions=json.loads(current['secret_versions_json'])
            with db:
                row=db.execute('SELECT COALESCE(MAX(version),0)+1 AS version FROM service_secrets WHERE service_id=? AND key=?',(service_id,key)).fetchone()
                version=row['version']
                db.execute('INSERT INTO service_secrets(service_id,key,version,ciphertext,created_at) VALUES(?,?,?,?,?)',
                    (service_id,key,version,ciphertext,now))
                versions[key]=version
                number=db.execute('SELECT COALESCE(MAX(number),0)+1 FROM service_revisions WHERE service_id=?',(service_id,)).fetchone()[0]
                revision_id=db.execute('INSERT INTO service_revisions(service_id,number,spec_json,secret_versions_json,created_by,created_at) VALUES(?,?,?,?,?,?)',
                    (service_id,number,current['spec_json'],json.dumps(versions,ensure_ascii=False),created_by,now)).lastrowid
                db.execute('UPDATE services SET current_revision_id=?,updated_at=? WHERE id=?',(revision_id,now,service_id))
                result=db.execute('SELECT * FROM service_revisions WHERE id=?',(revision_id,)).fetchone()
        return self._revision(result)

    def delete_service(self,service_id: int) -> bool:
        with self._db() as db:
            active=db.execute("SELECT EXISTS(SELECT 1 FROM deployments WHERE service_id=? AND status IN ('pending_approval','queued','applying','verifying'))",(service_id,)).fetchone()[0]
            if active:return False
            db.execute('DELETE FROM service_secrets WHERE service_id=?',(service_id,))
            db.execute('DELETE FROM service_revisions WHERE service_id=?',(service_id,))
            return db.execute('DELETE FROM services WHERE id=?',(service_id,)).rowcount>0
