import hashlib
import json
from pathlib import Path
from decimal import Decimal
from datetime import date, datetime
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.utils import timezone
from .models import *
from .permissions import allowed


def can_record(user,project):
    return allowed(user,project) and not project.archived and (user.is_superuser or project.memberships.filter(user=user,role='记录人员').exists())
def require_recorder(user,project):
    if not can_record(user,project): raise PermissionDenied('仅项目记录人员可登记；归档项目只读。')
def serial(value):
    return json.loads(json.dumps(value,ensure_ascii=False,default=lambda x: x.isoformat() if isinstance(x,(date,datetime)) else str(x)))
def state(obj): return serial(model_to_dict(obj))
def log(user,project,action,detail): Audit.objects.create(actor=user,project=project,action=action,detail=detail)
def journal(user,obj,kind,action,payload,before=None):
    event=Journal.objects.create(project=obj.project,entity_type=kind,entity_id=obj.pk,actor=user,action=action,payload=serial(payload),snapshot={'before':before or {},'after':state(obj)})
    log(user,obj.project,action,f'{kind} #{obj.pk} · {json.dumps(event.payload,ensure_ascii=False)}')
    return event

def validate_materials(screenshots,documents):
    for files,limit,extensions in [(screenshots,5,{'png','jpg','jpeg','webp'}),(documents,20,{'pdf','doc','docx','xls','xlsx','txt','zip'})]:
        for file in files:
            if file.size>limit*1024*1024: raise ValidationError(f'{file.name} 超过 {limit} MB 上限。')
            if Path(file.name).suffix.lower().lstrip('.') not in extensions: raise ValidationError(f'{file.name} 文件类型不支持。')
            if files is screenshots:
                head=file.read(12); file.seek(0)
                if not (head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff') or (head[:4]==b'RIFF' and head[8:12]==b'WEBP')): raise ValidationError('截图文件内容与图片格式不符。')
                try:
                    from PIL import Image
                    import io
                    with Image.open(io.BytesIO(file.read())) as picture: picture.verify()
                except (OSError,ValueError,Image.DecompressionBombError): raise ValidationError('截图无法解码或图片尺寸过大。')
                finally: file.seek(0)

def save_materials(user,req,data,stage,round,record=None,event=None,confirmation=None):
    screenshots=data.get('screenshots',[]); documents=data.get('documents',[])
    validate_materials(screenshots,documents)
    for kind,files in [('screenshot',screenshots),('document',documents)]:
        for file in files:
            Attachment.objects.create(requirement=req,record=record,bug_event=event,confirmation=confirmation,kind=kind,stage=stage,round=round,version=data.get('version',data.get('discovered_version','')),file=file,name=Path(file.name).name,actor=user)
            log(user,req.project,'上传附件',f'{req.number} · 第 {round} 轮 · {kind} · {Path(file.name).name}')

def add_confirmation(user,req,data,stage,round):
    if not any([data.get('confirmer'),data.get('summary'),data.get('checked'),data.get('conclusion') not in [None,'pending'],data.get('screenshots')]): return None
    if stage=='10': raise ValidationError('确认材料应关联具体业务环节，请选择补充确认材料所属环节。')
    if round<1 or round>req.round: raise ValidationError('材料所属轮次无效。')
    if data.get('checked') and (not data.get('confirmer') or not data.get('summary') or data.get('conclusion')=='pending'): raise ValidationError('请填写确认人、明确结论和确认摘要。')
    # New confirmation can reference an earlier screenshot for the same round/stage/version.
    has_image=bool(data.get('screenshots')) or req.attachments.filter(stage=stage,round=round,kind='screenshot',version=data.get('version','')).exists()
    if data.get('checked') and not has_image: raise ValidationError('完成材料核对需要本次上传或已留存的同环节、同轮次、同版本截图。')
    return Confirmation.objects.create(requirement=req,stage=stage,round=round,version=data.get('version',''),confirmer=data.get('confirmer',''),confirmed_at=data.get('confirmed_at'),conclusion=data.get('conclusion','pending'),summary=data.get('summary',''),checked=data.get('checked',False),actor=user)

def evidence(req):
    # One evidence status for each reached business stage of the current processing round.
    required=STAGES[:min(int(req.stage)+1,10)]
    latest={}
    for item in req.confirmations.filter(round=req.round).order_by('-created_at','-pk'):
        latest.setdefault(item.stage,item)
    rows=[]
    for key,label in required:
        conf=latest.get(key)
        complete=bool(conf and conf.checked and conf.conclusion=='confirmed' and conf.confirmer and conf.summary)
        rows.append({'stage':key,'label':label,'complete':complete,'confirmation':conf})
    return {'rows':rows,'covered':sum(x['complete'] for x in rows),'required':len(rows),'missing':[x['label'] for x in rows if not x['complete']]}

@transaction.atomic
def create_requirement(user,data):
    project=Project.objects.select_for_update().get(pk=data['project'].pk); require_recorder(user,project)
    validate_materials(data.get('screenshots',[]),data.get('documents',[]))
    req=Requirement.objects.create(project=project,creator=user,owner=None,**{key:data.get(key) or '' for key in ['title','body','priority','proposer','business_owner','version','source_group']},due_date=data.get('due_date'))
    conf=add_confirmation(user,req,data,'0',1)
    rec=Record.objects.create(requirement=req,stage='0',round=1,actor=user,owner=None,action='登记需求',note=req.body or '登记新需求',version=req.version,snapshot=state(req),payload={'business_owner':req.business_owner,'proposer':req.proposer},incomplete=not bool(conf and conf.checked and conf.conclusion=='confirmed'))
    save_materials(user,req,data,'0',1,record=rec,confirmation=conf)
    log(user,project,'登记需求',str(req)); return req

@transaction.atomic
def record_progress(user,pk,data):
    req=Requirement.objects.select_for_update().select_related('project').get(pk=pk); require_recorder(user,req.project)
    if data['revision']!=req.records.count(): raise ValidationError('记录已更新，请刷新后重试。')
    if data['stage'] not in dict(STAGES) or data['save_mode'] not in ['append','update'] or data['round_mode'] not in ['keep','new']: raise ValidationError('处理方式无效。')
    if not data.get('note','').strip() or not data.get('business_owner','').strip(): raise ValidationError('请填写进展及业务负责人。')
    before=state(req)
    if data['round_mode']=='new':
        if data['save_mode']!='update': raise ValidationError('新轮次必须同步更新业务进度。')
        req.round+=1
    validate_materials(data.get('screenshots',[]),data.get('documents',[]))
    evidence_stage=data.get('evidence_stage') or data['stage']; evidence_round=data.get('evidence_round') or req.round
    if not 1<=evidence_round<=req.round: raise ValidationError('材料所属轮次不存在。')
    if data['save_mode']=='update':
        req.stage=data['stage']; req.business_owner=data['business_owner']; req.version=data.get('version','')
        if data.get('expected_on'): req.due_date=data['expected_on']
    conf=add_confirmation(user,req,data,evidence_stage,evidence_round)
    payload={k:serial(v) for k,v in data.items() if k not in ['screenshots','documents','revision']}
    rec=Record.objects.create(requirement=req,stage=data['stage'],round=req.round,actor=user,owner=None,action='更新业务进度' if data['save_mode']=='update' else '追加进展',note=data['note'],version=data.get('version',''),occurred_at=data.get('occurred_at'),payload=payload,snapshot=before,incomplete=not bool(conf and conf.checked and conf.conclusion=='confirmed'))
    save_materials(user,req,data,evidence_stage,evidence_round,record=rec,confirmation=conf)
    req.save(); log(user,req.project,rec.action,f'{req.number} · {data["note"]}'); return req

@transaction.atomic
def record_bug(user,data,pk=None):
    req=Requirement.objects.select_for_update().select_related('project').get(pk=data['requirement'].pk); require_recorder(user,req.project)
    validate_materials(data.get('screenshots',[]),data.get('documents',[]))
    bug=Bug.objects.select_for_update().get(pk=pk) if pk else Bug(requirement=req,round=req.round)
    if pk and (bug.requirement_id!=req.pk or data.get('revision')!=bug.events.count()): raise ValidationError('Bug 记录已变化，请刷新后重试。')
    if data['status'] not in dict(Bug.STATES): raise ValidationError('Bug 状态无效。')
    if not data.get('provider','').strip() or not data.get('note','').strip() or not data.get('business_owner','').strip(): raise ValidationError('请填写负责人、结果提供人和处理说明。')
    before=state(bug) if pk else {}; previous=bug.status if pk else None
    for field in ['title','discovery_stage','status','business_owner','discovered_version']: setattr(bug,field,data.get(field,''))
    if bug.status in ['verify','closed']: bug.fix_version=data.get('discovered_version','')
    bug.save()
    action='登记 Bug' if not pk else ('重新打开' if previous=='closed' and bug.status!='closed' else '追加 Bug 记录')
    payload={k:serial(v) for k,v in data.items() if k not in ['screenshots','documents','requirement','revision']}; payload['before']=before
    event=BugEvent.objects.create(bug=bug,actor=user,action=action,note=data['note'],version=data.get('discovered_version',''),payload=payload,occurred_at=data.get('occurred_at'))
    save_materials(user,req,data,bug.discovery_stage,bug.round,event=event)
    log(user,req.project,action,f'{bug.number} · 结果提供人 {data["provider"]} · {data["note"]}')
    return bug

ENTITY_MODELS={'reviews':Review,'data':DailyData,'anomalies':Anomaly,'assets':Asset}
@transaction.atomic
def create_entity(user,kind,data):
    project=Project.objects.select_for_update().get(pk=data['project'].pk); require_recorder(user,project)
    model=ENTITY_MODELS[kind]
    fields={f.name for f in model._meta.fields}-{'id','created_at','updated_at','status','source_type'}
    obj=model(**{k:v for k,v in data.items() if k in fields})
    if kind=='data': obj.status='verified' if data.get('verified') else 'pending'
    obj.full_clean(); obj.save(); journal(user,obj,kind,'登记记录',{k:v for k,v in state(obj).items() if k!='project'}); return obj

@transaction.atomic
def update_entity(user,kind,pk,data):
    obj=ENTITY_MODELS[kind].objects.select_for_update().select_related('project').get(pk=pk); require_recorder(user,obj.project)
    if data.get('revision')!=Journal.objects.filter(entity_type=kind,entity_id=pk).count(): raise ValidationError('记录已更新，请刷新后重试。')
    before=state(obj)
    if kind=='data':
        keys=['provider','visits','registrations','cost','revenue','source','note']
        changed=any(serial(data.get(k))!=before.get(k) for k in keys)
        if changed and not data.get('reason','').strip(): raise ValidationError('修改数据时必须填写更正原因。')
        for key in keys: setattr(obj,key,data.get(key))
        obj.status='verified' if data.get('verified') else 'pending'
    elif kind=='assets':
        if not data.get('verified'): raise ValidationError('必须核实实际续费结果后才能更新到期日。')
        if data['expires_on']<=obj.expires_on: raise ValidationError('新到期日必须晚于原到期日。')
        if data['cost']<0: raise ValidationError('金额不能为负数。')
        obj.expires_on=data['expires_on']; obj.cost=data['cost']
    else:
        if data['status'] not in dict(obj._meta.get_field('status').choices): raise ValidationError('状态无效。')
        obj.status=data['status']
    obj.full_clean(); obj.save()
    journal(user,obj,kind,{'data':'核对 / 更正日报','assets':'登记续费','reviews':'追加审查反馈','anomalies':'追加核查'}[kind],{k:v for k,v in data.items() if k not in ['project','revision']},before)
    return obj

@transaction.atomic
def ingest(connection,payload):
    """Push ingestion; idempotent request IDs, one effective row per project/day/channel."""
    connection=DataConnection.objects.select_for_update().select_related('project').get(pk=connection.pk)
    if not connection.enabled or connection.project.archived: raise PermissionDenied
    request_id=payload.get('request_id'); rows=payload.get('rows')
    if not isinstance(request_id,str) or not request_id.strip() or len(request_id)>100 or not isinstance(rows,list) or not 1<=len(rows)<=500: raise ValidationError('需提供 request_id（1–100 字符）和 1–500 条 rows。')
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    old=SyncRun.objects.filter(connection=connection,request_id=request_id).first()
    if old:
        if old.payload_hash!=digest: raise ValidationError('同一 request_id 不可提交不同内容。')
        return old
    prepared=[]; keys=set()
    for row in rows:
        if not isinstance(row,dict): raise ValidationError('每条数据必须是对象。')
        try: day=date.fromisoformat(row.get('day',''))
        except (ValueError,TypeError): raise ValidationError('day 必须为 YYYY-MM-DD。')
        channel=row.get('channel','')
        if not isinstance(channel,str) or not channel.strip() or len(channel)>100: raise ValidationError('渠道不能为空且最多 100 字符。')
        key=(day,channel)
        if key in keys: raise ValidationError('同一批次含重复日期与渠道。')
        keys.add(key)
        for field in ['visits','registrations']:
            value=row.get(field)
            if value is not None and (isinstance(value,bool) or not isinstance(value,int) or value<0): raise ValidationError(f'{field} 必须是非负整数或 null。')
        candidate=DailyData(project=connection.project,day=day,channel=channel,provider=connection.name,source_type='api',source=str(row.get('source') or f'{connection.name} / {request_id}'),visits=row.get('visits'),registrations=row.get('registrations'),cost=row.get('cost'),revenue=row.get('revenue'))
        candidate.full_clean(validate_unique=False,validate_constraints=False); prepared.append(candidate)
    run=SyncRun.objects.create(connection=connection,request_id=request_id,payload_hash=digest)
    # Source identity is retained in the sync event, and API cannot self-approve data.
    for candidate in prepared:
        old=DailyData.objects.select_for_update().filter(project=connection.project,day=candidate.day,channel=candidate.channel).first()
        if old:
            before=state(old)
            same=all(getattr(old,k)==getattr(candidate,k) for k in ['visits','registrations','cost','revenue'])
            if not same:
                old.status='conflict'; old.save(); run.conflicts+=1
            Journal.objects.create(project=connection.project,entity_type='data',entity_id=old.pk,actor=connection.project.owner,action='接口数据比对',payload={'connection':connection.name,'request_id':request_id,'incoming':state(candidate),'same':same},snapshot={'before':before,'after':state(old)})
        else:
            candidate.save(); Journal.objects.create(project=connection.project,entity_type='data',entity_id=candidate.pk,actor=connection.project.owner,action='接口同步',payload={'connection':connection.name,'request_id':request_id},snapshot={'after':state(candidate)})
        run.count+=1
    run.save(); return run
