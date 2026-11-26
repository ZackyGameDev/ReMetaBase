"""Persist extracted metadata, snapshots, and schema change records."""

import hashlib
import json
from typing import Any

from sqlalchemy import text

from schemas import DatabaseSyncResult, DatabaseTargetConfig
from services.catalog_db import get_catalog_engine


def _checksum(result: DatabaseSyncResult) -> str:
    signature = [(t.table_name, [(c.name, c.raw_type, c.nullable, c.is_primary_key)
                                  for c in t.columns])
                 for t in sorted(result.tables, key=lambda table: table.table_name)]
    return hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()


def save_sync_to_catalog(target: DatabaseTargetConfig, result: DatabaseSyncResult) -> None:
    if result.status != 'success':
        return
    with get_catalog_engine().begin() as conn:
        source = conn.execute(text("""SELECT source_id FROM data_source
            WHERE db_type=:dialect AND COALESCE(host,'')=:host AND COALESCE(port,0)=:port
              AND database_name=:database"""),
            {'dialect': target.dialect, 'host': target.host or '', 'port': target.port or 0,
             'database': target.database}).scalar()
        if source is None:
            source = conn.execute(text("""INSERT INTO data_source
                (name,db_type,host,port,database_name,username,status,last_extracted_at)
                VALUES (:name,:dialect,:host,:port,:database,:username,'ACTIVE',NOW()) RETURNING source_id"""),
                {'name': target.label, 'dialect': target.dialect, 'host': target.host,
                 'port': target.port, 'database': target.database, 'username': target.username}).scalar()
        else:
            conn.execute(text("UPDATE data_source SET name=:name,username=:username,status='ACTIVE',last_extracted_at=NOW() WHERE source_id=:id"),
                         {'name': target.label, 'username': target.username, 'id': source})
        schema = conn.execute(text("""INSERT INTO schema_meta(source_id,schema_name) VALUES (:source,:name)
            ON CONFLICT(source_id,schema_name) DO UPDATE SET schema_name=EXCLUDED.schema_name RETURNING schema_id"""),
            {'source': source, 'name': result.schema_name}).scalar()
        previous = conn.execute(text("SELECT version_no,checksum FROM schema_snapshot WHERE source_id=:id ORDER BY version_no DESC LIMIT 1"), {'id': source}).mappings().first()
        version = (previous["version_no"] + 1) if previous else 1
        checksum = _checksum(result)
        changes: list[dict[str, Any]] = []
        existing_tables = {row["table_name"]: row["table_id"] for row in conn.execute(text("SELECT table_name,table_id FROM table_meta WHERE schema_id=:id AND is_active"), {'id': schema}).mappings()}
        seen_tables: set[str] = set()
        for table in result.tables:
            seen_tables.add(table.table_name)
            if previous and table.table_name not in existing_tables:
                changes.append({'type':'TABLE','name':table.table_name,'action':'ADDED','old':None,'new':table.table_name})
            table_id = conn.execute(text("""INSERT INTO table_meta(schema_id,table_name,is_active) VALUES (:schema,:name,TRUE)
                ON CONFLICT(schema_id,table_name) DO UPDATE SET is_active=TRUE RETURNING table_id"""),
                {'schema':schema,'name':table.table_name}).scalar()
            old_cols = {row["column_name"]: (row["column_id"], row["raw_type"]) for row in conn.execute(text("SELECT column_name,column_id,raw_type FROM column_meta WHERE table_id=:id AND is_active"), {'id':table_id}).mappings()}
            seen_cols: set[str] = set()
            ids: dict[str,int] = {}
            for col in table.columns:
                seen_cols.add(col.name)
                native = col.raw_type[:100]
                mapping = conn.execute(text("""INSERT INTO type_mapping(dialect,native_type,unified_type) VALUES (:d,:n,:u)
                    ON CONFLICT(dialect,native_type) DO UPDATE SET unified_type=EXCLUDED.unified_type RETURNING type_mapping_id"""),
                    {'d':target.dialect,'n':native,'u':col.unified_type[:50]}).scalar()
                props = dict(col.dialect_extras, default=col.default, unique=col.unique,
                             check_constraints=col.check_constraints, null_fraction=col.null_fraction)
                cid = conn.execute(text("""INSERT INTO column_meta(table_id,type_mapping_id,column_name,raw_type,ordinal_position,is_primary_key,is_nullable,is_active,dialect_props)
                    VALUES (:tid,:map,:name,:raw,:pos,:pk,:nullable,TRUE,CAST(:props AS JSONB))
                    ON CONFLICT(table_id,column_name) DO UPDATE SET type_mapping_id=EXCLUDED.type_mapping_id,raw_type=EXCLUDED.raw_type,
                    ordinal_position=EXCLUDED.ordinal_position,is_primary_key=EXCLUDED.is_primary_key,is_nullable=EXCLUDED.is_nullable,is_active=TRUE,dialect_props=EXCLUDED.dialect_props
                    RETURNING column_id"""), {'tid':table_id,'map':mapping,'name':col.name,'raw':col.raw_type[:100],
                    'pos':col.ordinal_position,'pk':col.is_primary_key,'nullable':col.nullable,'props':json.dumps(props)}).scalar()
                ids[col.name] = cid
                if previous and col.name not in old_cols:
                    changes.append({'type':'COLUMN','name':f'{table.table_name}.{col.name}','action':'ADDED','old':None,'new':col.raw_type})
                elif previous and old_cols[col.name][1] != col.raw_type:
                    changes.append({'type':'COLUMN','name':f'{table.table_name}.{col.name}','action':'MODIFIED','old':old_cols[col.name][1],'new':col.raw_type})
            for name,(cid,raw) in old_cols.items():
                if name not in seen_cols:
                    conn.execute(text("UPDATE column_meta SET is_active=FALSE WHERE column_id=:id"), {'id':cid})
                    changes.append({'type':'COLUMN','name':f'{table.table_name}.{name}','action':'DROPPED','old':raw,'new':None})
            for fk_col, reference in ((c.name,c.foreign_key) for c in table.columns if c.foreign_key):
                if '.' not in reference or fk_col not in ids:
                    continue
                ref_table, ref_col = reference.split('.',1)
                parent = conn.execute(text("SELECT c.column_id FROM column_meta c JOIN table_meta t ON t.table_id=c.table_id WHERE t.schema_id=:schema AND t.table_name=:table AND c.column_name=:column AND c.is_active"), {'schema':schema,'table':ref_table,'column':ref_col}).scalar()
                if parent:
                    conn.execute(text("""INSERT INTO column_references(child_column_id,parent_column_id) VALUES (:child,:parent)
                        ON CONFLICT(child_column_id,parent_column_id) DO NOTHING"""), {'child':ids[fk_col],'parent':parent})
            existing_indexes = {row["index_name"]: row["index_id"] for row in conn.execute(text("SELECT index_name,index_id FROM index_meta WHERE table_id=:id"), {'id':table_id}).mappings()}
            current_indexes=set()
            for index in table.indexes:
                current_indexes.add(index.index_name)
                index_id=conn.execute(text("""INSERT INTO index_meta(table_id,index_name,is_unique) VALUES (:tid,:name,:unique)
                    ON CONFLICT(table_id,index_name) DO UPDATE SET is_unique=EXCLUDED.is_unique RETURNING index_id"""),
                    {'tid':table_id,'name':index.index_name[:150],'unique':index.is_unique}).scalar()
                conn.execute(text("DELETE FROM index_columns WHERE index_id=:id"), {'id':index_id})
                for pos,name in enumerate(index.columns,1):
                    if name in ids:
                        conn.execute(text("INSERT INTO index_columns(index_id,column_id,position) VALUES (:idx,:col,:pos) ON CONFLICT DO NOTHING"), {'idx':index_id,'col':ids[name],'pos':pos})
            for name,idxid in existing_indexes.items():
                if name not in current_indexes:
                    conn.execute(text("DELETE FROM index_meta WHERE index_id=:id"), {'id':idxid})
            conn.execute(text("""INSERT INTO operational_metric(table_id,row_count,null_fraction) VALUES (:id,:count,:fraction)"""),
                         {'id':table_id,'count':table.row_count,'fraction':table.null_fraction})
        for name, table_id in existing_tables.items():
            if name not in seen_tables:
                conn.execute(text("UPDATE table_meta SET is_active=FALSE WHERE table_id=:id"), {'id':table_id})
                changes.append({'type':'TABLE','name':name,'action':'DROPPED','old':name,'new':None})
        conn.execute(text("INSERT INTO schema_snapshot(source_id,version_no,checksum) VALUES (:source,:version,:checksum)"),
                     {'source':source,'version':version,'checksum':checksum})
        snapshot=conn.execute(text("SELECT snapshot_id FROM schema_snapshot WHERE source_id=:source AND version_no=:version"), {'source':source,'version':version}).scalar()
        if previous and previous["checksum"] != checksum and not changes:
            changes.append({'type':'SCHEMA','name':result.schema_name,'action':'MODIFIED','old':previous["checksum"],'new':checksum})
        for change in changes:
            conn.execute(text("""INSERT INTO schema_change_log(snapshot_id,object_type,object_name,change_type,old_value,new_value)
                VALUES (:snapshot,:type,:name,:action,:old,:new)"""),
                {'snapshot':snapshot,**change})


def fetch_catalog_state() -> list[dict[str, Any]]:
    with get_catalog_engine().connect() as conn:
        sources=conn.execute(text("SELECT source_id,name,db_type,last_extracted_at FROM data_source ORDER BY source_id")).mappings().all()
        output=[]
        for source in sources:
            tables=conn.execute(text("""SELECT t.table_id,t.table_name,COALESCE(m.row_count,0) row_count,COALESCE(m.null_fraction,0) null_fraction
                FROM table_meta t JOIN schema_meta s ON s.schema_id=t.schema_id
                LEFT JOIN LATERAL (SELECT row_count,null_fraction FROM operational_metric WHERE table_id=t.table_id ORDER BY profiled_at DESC LIMIT 1) m ON TRUE
                WHERE s.source_id=:source AND t.is_active ORDER BY t.table_name"""), {'source':source['source_id']}).mappings().all()
            table_out=[]
            for table in tables:
                columns=conn.execute(text("""SELECT c.column_id,c.column_name,c.raw_type,tm.unified_type,c.is_primary_key,c.is_nullable,c.ordinal_position,c.dialect_props,
                    pt.table_name || '.' || pc.column_name foreign_key
                    FROM column_meta c LEFT JOIN type_mapping tm ON tm.type_mapping_id=c.type_mapping_id
                    LEFT JOIN column_references cr ON cr.child_column_id=c.column_id LEFT JOIN column_meta pc ON pc.column_id=cr.parent_column_id
                    LEFT JOIN table_meta pt ON pt.table_id=pc.table_id WHERE c.table_id=:table AND c.is_active ORDER BY c.ordinal_position"""), {'table':table['table_id']}).mappings().all()
                table_out.append({**dict(table),'columns':[dict(c) for c in columns]})
            logs=conn.execute(text("""SELECT l.object_type,l.object_name,l.change_type,l.old_value,l.new_value,s.version_no,s.captured_at
                FROM schema_change_log l JOIN schema_snapshot s ON s.snapshot_id=l.snapshot_id WHERE s.source_id=:source ORDER BY l.change_id DESC LIMIT 25"""), {'source':source['source_id']}).mappings().all()
            output.append({'source_id':source['source_id'],'name':source['name'],'db_type':source['db_type'],
                'last_extracted_at':str(source['last_extracted_at']),'tables':table_out,
                'change_logs':[{**dict(log),'captured_at':str(log['captured_at'])} for log in logs]})
        return output
