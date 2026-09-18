from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.conf import settings
from .models import *
from .forms import members

from .permissions import STAGE_ROLES, allowed, has_role, can_process, requirement_actions, bug_actions

def audit(user,project,action,detail): Audit.objects.create(actor=user,project=project,action=action,detail=detail)
def notify(user,text,url): Notification.objects.create(user=user,text=text[:255],url=url)
def validate_file(file):
    if file and file.size>settings.MAX_ATTACHMENT_SIZE: raise ValidationError(f'附件超过单文件 {settings.MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB 上限。')
def attach(user,req,file,record=None,event=None):
    if not file: return
    validate_file(file)
    Attachment.objects.create(requirement=req,record=record,bug_event=event,stage=record.stage if record else ('5' if event else req.stage),round=record.round if record else (event.bug.round if event else req.round),file=file,name=file.name,actor=user)
    audit(user,req.project,'上传附件',f'{req.number} · {file.name}')
def snapshot(req): return {'title':req.title,'body':req.body,'priority':req.priority,'due_date':str(req.due_date or ''),'stage':req.stage,'owner_id':req.owner_id,'round':req.round}

@transaction.atomic
def process(user,pk,data):
    req=Requirement.objects.select_for_update().select_related('project').get(pk=pk)
    if not can_process(user,req): raise PermissionDenied('只有具备对应角色的当前负责人可以处理。')
    if req.project.archived: raise ValidationError('项目已归档，只能查看历史记录。')
    if data['revision']!=req.records.count(): raise ValidationError('记录已更新，请刷新页面后重新处理。')
    action=data['action']; receiver=data.get('receiver'); file=data.get('file'); validate_file(file)
    if action not in dict(requirement_actions(user,req)): raise ValidationError('当前状态不允许此需求操作。')
    if action=='note' and receiver and receiver.pk!=req.owner_id: raise ValidationError('补充记录不能改变负责人，请使用转交操作。')
    if not data.get('note','').strip(): raise ValidationError('请填写处理说明。')
    if action in ['advance','transfer'] and not receiver: raise ValidationError('请选择下一负责人。')
    if receiver and not members(req.project).filter(pk=receiver.pk).exists(): raise ValidationError('接收人必须是有效项目成员。')
    if req.stage=='10' and action!='note': raise ValidationError('已完成需求只能补充记录。')
    changes=any(data.get(field) for field in ['body','title','priority','due_date'])
    if changes and (action not in ['return','note'] or req.stage=='10'): raise ValidationError('需求内容只能在未完成时通过退回或补充记录更新。')
    if action=='return' and (not data.get('target') or int(data['target'])>=int(req.stage)): raise ValidationError('只能退回上游环节。')
    next_stage=str(int(req.stage)+1) if action=='advance' else (data.get('target') if action=='return' else req.stage)
    next_owner=receiver or req.owner
    if action in ['advance','return','transfer'] and next_stage!='10' and not has_role(next_owner,req.project,[STAGE_ROLES[int(next_stage)]]): raise ValidationError('接收人缺少目标环节对应的项目角色，请在后台配置。')
    if action=='advance' and req.stage=='5' and req.bugs.exclude(status='closed').exists() and not data.get('risk_reason'): raise ValidationError('仍有未关闭 Bug，必须填写风险放行原因。')
    if action=='advance' and req.stage in ['7','8'] and not data.get('commit'): raise ValidationError('代码上传与代码审核必须登记提交编号。')
    if action=='advance' and req.stage=='8':
        upload=req.records.filter(round=req.round,stage='7',action='确认通过').first()
        if not upload or upload.commit!=data['commit']: raise ValidationError('审核提交编号必须与本轮代码上传记录一致。')
    labels={'advance':'确认通过','return':'退回','transfer':'转交','note':'补充记录'}
    rec=Record.objects.create(requirement=req,stage=req.stage,round=req.round,actor=user,owner=req.owner,receiver=receiver,action=labels[action],note=data['note']+('\n风险放行原因：'+data['risk_reason'] if data.get('risk_reason') else ''),commit=data.get('commit',''),version=data.get('version',''),snapshot=snapshot(req),incomplete=not bool(file))
    attach(user,req,file,record=rec)
    if action=='return': req.round+=1
    req.stage=next_stage
    if receiver: req.owner=receiver
    for field in ['body','title','priority','due_date']:
        if data.get(field): setattr(req,field,data[field])
    if changes: audit(user,req.project,'修改需求字段',f'{req.number} · 修改前 {rec.snapshot} · 修改后 {snapshot(req)}')
    req.save()
    audit(user,req.project,labels[action],f'{req.number} · {rec.get_stage_display()} · 第 {rec.round} 轮 · {rec.note} · 原负责人 {rec.owner} → {req.owner}')
    notify(req.owner,f'{req.number} {labels[action]} · {req.get_stage_display()}',f'/requirements/{req.pk}/')
    return rec

@transaction.atomic
def act_bug(user,pk,data):
    bug=Bug.objects.select_for_update().select_related('requirement__project').get(pk=pk); req=bug.requirement
    if not allowed(user,req.project): raise PermissionDenied
    if req.project.archived: raise ValidationError('项目已归档。')
    if data['revision']!=bug.events.count(): raise ValidationError('Bug 已更新，请刷新后重试。')
    action=data['action']; receiver=data.get('receiver') or bug.owner
    if action not in dict(bug_actions(user,bug)):
        if action=='fix' and bug.status!='open' or action=='pass' and bug.status!='verify' or action=='fail' and bug.status not in ['verify','closed'] or action=='assign' and bug.status=='closed':
            raise ValidationError('当前 Bug 状态不允许此操作。')
        raise PermissionDenied('你没有执行此 Bug 操作的权限。')
    if not data.get('note','').strip(): raise ValidationError('请填写处理说明。')
    if not members(req.project).filter(pk=receiver.pk).exists(): raise ValidationError('接收人必须是有效项目成员。')
    validate_file(data.get('file'))
    if action=='fix':
        if not (user.is_superuser or (bug.owner_id==user.pk and has_role(user,req.project,['开发人员']))): raise PermissionDenied
        if bug.status!='open': raise ValidationError('只有待修复 Bug 可以提交修复。')
        if not data.get('commit') or not data.get('version'): raise ValidationError('请填写修复提交编号和待验证版本。')
        bug.status='verify'; bug.fix_version=data['version']
    elif action in ['pass','fail']:
        if not has_role(user,req.project,['测试负责人']): raise PermissionDenied
        if action=='pass' and bug.status!='verify': raise ValidationError('只有待回归 Bug 可以关闭。')
        if action=='fail' and bug.status not in ['verify','closed']: raise ValidationError('只有待回归或已关闭 Bug 可以重新打开。')
        if not data.get('version'): raise ValidationError('请登记回归测试版本。')
        bug.status='closed' if action=='pass' else 'open'
    elif action=='assign':
        if not (user.is_superuser or req.project.owner_id==user.pk or has_role(user,req.project,['测试负责人','分配负责人'])): raise PermissionDenied
        if not data.get('receiver'): raise ValidationError('请选择接收人。')
    labels={'fix':'提交修复','pass':'回归通过','fail':'重新打开','assign':'指派'}
    event=BugEvent.objects.create(bug=bug,actor=user,action=labels[action],note=data['note'],commit=data.get('commit',''),version=data.get('version',''),previous_owner=bug.owner,receiver=receiver)
    bug.owner=receiver; bug.save(); attach(user,req,data.get('file'),event=event)
    audit(user,req.project,labels[action],f'{bug.number} · {data["note"]} · {event.previous_owner} → {receiver}')
    notify(receiver,f'{bug.number} {labels[action]}',f'/bugs/{bug.pk}/')
    return event
