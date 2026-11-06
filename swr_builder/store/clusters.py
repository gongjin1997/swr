"""集群与命名空间。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from .schema import utcnow


class ClustersMixin:
    def list_clusters(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows=db.execute("SELECT id,name,auth_type,server,cce_cluster_id,credential_expires_at,cert_duration_days,status,created_at,updated_at FROM clusters ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(row) for row in rows]

    def get_cluster(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute("SELECT id,name,auth_type,server,cce_cluster_id,credential_expires_at,cert_duration_days,status,created_at,updated_at FROM clusters WHERE id=?",(cluster_id,)).fetchone()
        return dict(row) if row else None

    def get_cluster_record(self, cluster_id: int) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            row=db.execute("SELECT * FROM clusters WHERE id=?",(cluster_id,)).fetchone()
        return dict(row) if row else None

    def create_cluster(self, data: Dict[str, Any]) -> int:
        now=utcnow()
        with self._db() as db:
            cur=db.execute("INSERT INTO clusters(name,auth_type,server,credential_encrypted,cce_cluster_id,credential_expires_at,cert_duration_days,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (data['name'],data['auth_type'],data['server'],data['credential_encrypted'],data.get('cce_cluster_id'),data.get('credential_expires_at'),data.get('cert_duration_days',30),data.get('status','unverified'),now,now))
            return int(cur.lastrowid)

    def update_cluster(self, cluster_id: int, data: Dict[str, Any]) -> bool:
        fields=('name','auth_type','server','credential_encrypted','cce_cluster_id','credential_expires_at','cert_duration_days','status')
        values=[data.get(k) for k in fields]
        with self._db() as db:
            return db.execute("UPDATE clusters SET name=?,auth_type=?,server=?,credential_encrypted=?,cce_cluster_id=?,credential_expires_at=?,cert_duration_days=?,status=?,updated_at=? WHERE id=?",
                (*values,utcnow(),cluster_id)).rowcount>0

    def set_cluster_status(self, cluster_id: int, status: str) -> None:
        with self._db() as db:
            db.execute("UPDATE clusters SET status=?,updated_at=? WHERE id=?",(status,utcnow(),cluster_id))

    @staticmethod
    def _namespace(row: sqlite3.Row) -> Dict[str, Any]:
        item=dict(row)
        item['policy']=json.loads(item.pop('policy_json'))
        return item

    def list_namespaces(self, namespace_ids: Optional[List[int]] = None, cluster_id: Optional[int] = None,
                        tier: Optional[str] = None) -> List[Dict[str, Any]]:
        where,args=[],[]
        if namespace_ids is not None:
            if not namespace_ids:return []
            where.append(f"n.id IN ({','.join('?' for _ in namespace_ids)})");args.extend(namespace_ids)
        if cluster_id is not None:where.append('n.cluster_id=?');args.append(cluster_id)
        if tier is not None:where.append('n.tier=?');args.append(tier)
        clause=(' WHERE '+' AND '.join(where)) if where else ''
        with self._db() as db:
            rows=db.execute("SELECT n.*,c.name AS cluster_name FROM namespaces n JOIN clusters c ON c.id=n.cluster_id"+clause+" ORDER BY c.name,n.name",args).fetchall()
        return [self._namespace(row) for row in rows]

    def get_namespace(self, namespace_id: int) -> Optional[Dict[str, Any]]:
        rows = self.list_namespaces([namespace_id])
        return rows[0] if rows else None

    def create_namespace(self, cluster_id: int, name: str, tier: str, policy: Dict[str, Any]) -> int:
        now=utcnow()
        with self._db() as db:
            cur=db.execute("INSERT INTO namespaces(cluster_id,name,tier,policy_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (cluster_id,name,tier,json.dumps(policy,ensure_ascii=False),now,now))
            return int(cur.lastrowid)

    def update_namespace(self, namespace_id: int, tier: str, policy: Dict[str, Any]) -> bool:
        with self._db() as db:
            return db.execute("UPDATE namespaces SET tier=?,policy_json=?,updated_at=? WHERE id=?",
                (tier,json.dumps(policy,ensure_ascii=False),utcnow(),namespace_id)).rowcount>0

    def delete_namespace(self, namespace_id: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM namespaces WHERE id=?",(namespace_id,)).rowcount>0
