"""Superadmin view of provisioned employee accounts and center grants."""
from api.auth.workbuddy.types import DepartmentNode, DepartmentPath
from api.db.db_models import User, WorkBuddyEmployeeIdentity as I, EnterpriseDepartment as D, Knowledgebase
from api.db.services.center_dataset_service import CenterDatasetService as Centers


class WorkBuddyAdminService:
    @staticmethod
    def _employee(row):
        user=User.get_by_id(row.user_id)
        return {**{key:getattr(row,key) for key in ('user_id','name','alias_id','enterprise_id','directory_status','primary_department_id','primary_department_name','center_id','center_name','department_path','department_paths','sync_status','profile_synced_at','last_seen_at')},'account_source':'workbuddy','enabled':user.is_active=='1' and user.status=='1'}

    @classmethod
    def employees(cls,enterprise_id,*,q='',center_id=None,enabled=None,page=1,page_size=30):
        query=I.select().join(User,on=(I.user_id==User.id)).where((I.enterprise_id==enterprise_id)&(User.account_source=='workbuddy'))
        if q: query=query.where(I.name.contains(q)|I.alias_id.contains(q))
        if center_id: query=query.where(I.center_id==center_id)
        if enabled is not None: query=query.where(User.is_active==('1' if enabled else '0'))
        total=query.count()
        return {'items':[cls._employee(row) for row in query.order_by(I.name,I.user_id).paginate(page,page_size)],'total':total}

    @classmethod
    def employee(cls,enterprise_id,user_id):
        row=I.get_or_none(enterprise_id=enterprise_id,user_id=user_id)
        if row is None:
            raise ValueError('Employee not found')
        data=cls._employee(row)
        paths=tuple(DepartmentPath(p['department_id'],p['is_main'],p['complete'],tuple(DepartmentNode(**n) for n in p['hierarchy'])) for p in row.department_paths)
        center=Centers.resolve_center(enterprise_id,paths)
        data['allowed_dataset_ids']=sorted(Centers.allowed_datasets(enterprise_id,center)) if data['enabled'] else []
        return data

    @staticmethod
    def departments(enterprise_id,centers_only=False):
        query=D.select().where(D.enterprise_id==enterprise_id)
        if centers_only: query=query.where(D.is_center)
        return [{key:getattr(row,key) for key in ('department_id','name','parent_id','is_center','synced_at')} for row in query.order_by(D.name,D.department_id)]

    @staticmethod
    def datasets(*,q='',page=1,page_size=30):
        query=Knowledgebase.select().where(Knowledgebase.status=='1')
        if q: query=query.where(Knowledgebase.name.contains(q))
        return {'items':[{'id':kb.id,'name':kb.name,'owner':kb.tenant_id} for kb in query.order_by(Knowledgebase.name,Knowledgebase.id).paginate(page,page_size)],'total':query.count()}
