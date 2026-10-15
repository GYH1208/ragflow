"""Create MCP-only accounts, preserving local administrative state."""
from dataclasses import asdict
from datetime import datetime, timezone
from peewee import IntegrityError
from api.db.db_models import User, WorkBuddyEmployeeIdentity, EnterpriseDepartment
from common.misc_utils import get_uuid


class WorkBuddyEmployeeService:
    @staticmethod
    def sync_employee(profile):
        if profile.directory_status not in ('Enable','Enabled'):
            raise ValueError('Employee account is not enabled')
        identity=WorkBuddyEmployeeIdentity.get_or_none(enterprise_id=profile.enterprise_id,union_id=profile.union_id)
        if identity is None:
            try:
                with User._meta.database.atomic():
                    user_id=get_uuid()
                    User.create(id=user_id,nickname=profile.name,email=f'wb-{user_id}@workbuddy.invalid',account_source='workbuddy',login_channel='workbuddy',is_superuser=False)
                    identity=WorkBuddyEmployeeIdentity.create(id=get_uuid(),user_id=user_id,enterprise_id=profile.enterprise_id,union_id=profile.union_id,issuer=profile.issuer,application_id=profile.application_id,name=profile.name)
            except IntegrityError:
                identity=WorkBuddyEmployeeIdentity.get_or_none(enterprise_id=profile.enterprise_id,union_id=profile.union_id)
                if identity is None:
                    raise
        user=User.get_by_id(identity.user_id)
        if user.account_source!='workbuddy':
            raise ValueError('Invalid external account mapping')
        primary=[p for p in profile.paths if p.is_main]
        selected=primary[0] if len(primary)==1 else None
        centers={d.department_id:d for d in EnterpriseDepartment.select().where((EnterpriseDepartment.enterprise_id==profile.enterprise_id)&EnterpriseDepartment.is_center)}
        hits=[n for n in selected.hierarchy if n.id in centers] if selected and selected.complete and all(p.complete for p in profile.paths) else []
        center=hits[0] if len(hits)==1 else None
        display=list(selected.hierarchy) if selected else []
        if center:
            display=display[display.index(center):]
        values=dict(name=profile.name,alias_id=profile.alias_id,issuer=profile.issuer,application_id=profile.application_id,directory_status=profile.directory_status,
            department_paths=[asdict(p) for p in profile.paths],primary_department_id=selected.department_id if selected else None,
            primary_department_name=selected.hierarchy[-1].name if selected and selected.hierarchy else None,
            center_id=center.id if center else None,center_name=center.name if center else None,
            department_path=' / '.join(n.name for n in display) or None,
            sync_status='complete' if center else 'pending')
        now=datetime.now(timezone.utc).replace(tzinfo=None)
        updates={k:v for k,v in values.items() if getattr(identity,k)!=v}
        if updates or identity.profile_synced_at is None:
            updates['profile_synced_at']=now
        if identity.last_seen_at is None or (now-identity.last_seen_at).total_seconds()>=60:
            updates['last_seen_at']=now
        if updates:
            WorkBuddyEmployeeIdentity.update(**updates).where(WorkBuddyEmployeeIdentity.id==identity.id).execute()
        if user.nickname!=profile.name:
            User.update(nickname=profile.name).where(User.id==user.id).execute()
            user.nickname=profile.name
        return user

    @staticmethod
    def set_enabled(user_id,enabled,actor_id):
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        with User._meta.database.atomic():
            user=User.get_or_none(id=user_id,account_source='workbuddy')
            identity=WorkBuddyEmployeeIdentity.get_or_none(user_id=user_id)
            if user is None or identity is None:
                raise ValueError('WorkBuddy employee not found')
            User.update(is_active='1' if enabled else '0').where(User.id==user_id).execute()
            WorkBuddyEmployeeIdentity.update(enabled_updated_by=actor_id,enabled_updated_at=datetime.now(timezone.utc).replace(tzinfo=None)).where(WorkBuddyEmployeeIdentity.id==identity.id).execute()
