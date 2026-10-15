"""Online token validation and live directory lookup against pinned OneID URLs."""
import asyncio
import json
import re
import time
import httpx
from .types import DepartmentNode, DepartmentPath, VerifiedEmployee, WorkBuddyAuthenticationError, WorkBuddyProviderUnavailable

CONTACTS='https://api.account.tencent.com/openapi/v3/contacts/'


def _numeric(value):
    if type(value) is int:
        value=str(value)
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,20}',value) or int(value)<=0:
        raise ValueError('Invalid department identifier')
    return value


class OneIDClient:
    def __init__(self, config, transport=None):
        self.config=config
        self._client=httpx.AsyncClient(timeout=5,follow_redirects=False,transport=transport)
        self._metadata_until=0
        self._app_token=''
        self._app_token_until=0
        self._lock=asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self,*args):
        await self.close()

    async def close(self):
        await self._client.aclose()

    async def _json(self,url, *, token=None, form=None):
        try:
            async with self._client.stream('POST' if form is not None else 'GET',url,headers={'Authorization':'Bearer '+token} if token else {},data=form) as res:
                if res.status_code!=200:
                    raise WorkBuddyProviderUnavailable('OneID request failed')
                body=bytearray()
                async for part in res.aiter_bytes():
                    body.extend(part)
                    if len(body)>262144:
                        raise WorkBuddyProviderUnavailable('OneID response exceeded limit')
                data=json.loads(body)
                if not isinstance(data,dict):
                    raise WorkBuddyProviderUnavailable('Invalid OneID response')
                return data
        except (httpx.HTTPError,ValueError):
            raise WorkBuddyProviderUnavailable('OneID service unavailable') from None

    async def _metadata(self):
        async with self._lock:
            if time.monotonic()<self._metadata_until:
                return
            issuer=self.config.issuer
            data=await self._json(issuer+'/.well-known/openid-configuration')
            if (data.get('issuer')!=issuer or data.get('introspection_endpoint')!=issuer+'/introspect'
                    or data.get('userinfo_endpoint')!=issuer+'/userinfo'
                    or 'client_secret_post' not in data.get('introspection_endpoint_auth_methods_supported',[])):
                raise WorkBuddyProviderUnavailable('Untrusted OneID metadata')
            self._metadata_until=time.monotonic()+600

    async def _application_token(self):
        async with self._lock:
            if time.monotonic()<self._app_token_until:
                return self._app_token
            data=await self._json(self.config.issuer+'/token',form={'grant_type':'client_credentials','client_id':self.config.application_id,'client_secret':self.config.client_secret})
            access,expires=data.get('access_token'),data.get('expires_in')
            if not isinstance(access,str) or not access or type(expires) is not int or expires<=0:
                raise WorkBuddyProviderUnavailable('OneID application credential unavailable')
            self._app_token=access
            self._app_token_until=time.monotonic()+max(0,min(expires-30,300))
            return access

    async def _path(self,member,token,cache):
        nodes=[]
        seen=set()
        complete=False
        try:
            leaf=_numeric(member.get('department_id'))
            current=leaf
            for _ in range(24):
                if current in seen:
                    break
                seen.add(current)
                if current not in cache:
                    response=await self._json(CONTACTS+'departments/'+current,token=token)
                    node=response.get('data',{}).get('department',{})
                    if type(response.get('code')) is not int or response['code']!=0 or _numeric(node.get('department_id'))!=current:
                        raise ValueError('Invalid department')
                    cache[current]=node
                node=cache[current]
                name=node.get('department_name')
                if not isinstance(name,str) or not 0<len(name)<=256:
                    raise ValueError('Invalid department name')
                parent=node.get('department_parent_id')
                root=parent is None or parent in ('','0') or type(parent) is int and parent==0
                parent_id=None if root else _numeric(parent)
                nodes.append(DepartmentNode(current,name,parent_id))
                if 'department_parent_id' not in node:
                    if '_roots' not in cache:
                        roots=await self._json(CONTACTS+'departments/roots',token=token)
                        values=roots.get('data',{}).get('roots')
                        if type(roots.get('code')) is not int or roots['code']!=0 or not isinstance(values,list):
                            raise ValueError('Invalid roots')
                        cache['_roots']={_numeric(x.get('department_id')) for x in values}
                    complete=current in cache['_roots']
                    break
                if root:
                    complete=True
                    break
                current=parent_id
        except (ValueError,TypeError,AttributeError,WorkBuddyProviderUnavailable):
            leaf=str(member.get('department_id',''))[:64]
        return DepartmentPath(leaf,member.get('is_main_department') is True,complete,tuple(reversed(nodes)))

    async def verify_employee(self,token):
        if not self.config.enabled or not isinstance(token,str) or not token or len(token)>32768 or ',' in token or any(c.isspace() for c in token):
            raise WorkBuddyAuthenticationError('Invalid employee credential')
        try:
            await self._metadata()
            conf=self.config
            info=await self._json(conf.issuer+'/introspect',form={'token':token,'token_type_hint':'access_token','client_id':conf.application_id,'client_secret':conf.client_secret})
            now=time.time()
            if (info.get('active') is not True or info.get('client_id') not in conf.requesting_clients
                    or info.get('aud') not in (conf.application_id,[conf.application_id])
                    or info.get('iss')!=conf.issuer or info.get('tid')!=conf.enterprise_id
                    or type(info.get('exp')) is not int or info['exp']<=now
                    or any(type(info[k]) is not int or info[k]>now for k in ('iat','nbf') if k in info)
                    or str(info.get('token_type','Bearer')).lower()!='bearer'):
                raise WorkBuddyAuthenticationError('Employee credential validation failed')
            subject=info.get('sub')
            if not isinstance(subject,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',subject) or subject in conf.requesting_clients or subject==conf.application_id:
                raise WorkBuddyAuthenticationError('Employee subject required')
            profile=await self._json(conf.issuer+'/userinfo',token=token)
            tids=[profile[k] for k in ('tid','account_id') if k in profile]
            if profile.get('sub')!=subject or not tids or any(t!=conf.enterprise_id for t in tids):
                raise WorkBuddyAuthenticationError('Employee profile mismatch')
            app=await self._application_token()
            result=await self._json(CONTACTS+'users/'+subject+'?ignore_status=false',token=app)
            user=result.get('data',{}).get('user',{})
            if type(result.get('code')) is not int or result['code']!=0 or user.get('union_id')!=subject or user.get('status') not in ('Enable','Enabled'):
                raise WorkBuddyAuthenticationError('Employee directory account is not enabled')
            members=user.get('departments',[])
            if not isinstance(members,list) or len(members)>16 or any(not isinstance(m,dict) for m in members):
                members=[]
            cache={}
            paths=tuple([await self._path(m,app,cache) for m in members])
            name=user.get('name') or profile.get('name') or subject
            alias=user.get('alias_id') or profile.get('alias_id')
            if not isinstance(name,str) or not 0<len(name)<=100 or alias is not None and (not isinstance(alias,str) or len(alias)>128):
                raise WorkBuddyAuthenticationError('Invalid employee profile')
            return VerifiedEmployee(conf.enterprise_id,subject,conf.issuer,conf.application_id,alias,name,user['status'],paths)
        except (KeyError,TypeError,AttributeError,ValueError):
            raise WorkBuddyAuthenticationError('Invalid OneID identity response') from None
